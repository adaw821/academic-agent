"""HTTP checks with real cookie authentication and isolated in-memory SQLite."""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.dict(os.environ, {'DATABASE_URL': 'sqlite://'}):
    from app.db.database import Base, get_db
    from app.routers.assignments import router
for name in ('users','courses','course_timetable','projects','documents','document_parents','document_child','user_session'):
    importlib.import_module('app.models.' + name)
from app.models.users import User
from app.models.courses import Course
from app.models.assignments import Assignment
from app.models.user_session import UserSession
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
import httpx


@event.listens_for(UserSession, 'load')
def sqlite_timezone(value, context):
    if context.session.get_bind().dialect.name == 'sqlite' and value.expires_at.tzinfo is None:
        value.expires_at = value.expires_at.replace(tzinfo=timezone.utc)


class AssignmentQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine, tables=[User.__table__, Course.__table__, Assignment.__table__, UserSession.__table__])
        with Session(self.engine) as db:
            db.execute(User.__table__.insert(), [dict(id=i, username=f'u{i}', email=f'u{i}@test', password_hash='unused') for i in (1,2)])
            db.execute(Course.__table__.insert(), [dict(id=i,user_id=i,course_name=f'course{i}') for i in (1,2)])
            for token, uid, expiry in [('one',1,1),('two',2,1),('expired',1,-1)]:
                db.execute(UserSession.__table__.insert().values(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=uid, expires_at=datetime.now(timezone.utc)+timedelta(days=expiry)))
            for ident, course, due, status in [(1,1,datetime(2026,10,13,0,0),'No submissions have been made yet'),(2,1,datetime(2026,10,13,23,59),'Submitted for grading'),(3,1,datetime(2026,10,14,0,0),'Draft (not submitted)'),(4,1,None,None),(5,2,datetime(2026,10,13),'No submissions have been made yet')]:
                db.execute(Assignment.__table__.insert().values(id=ident,course_id=course,external_activity_id=str(ident),title=f'lab{ident}',source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id='+str(ident),due_at=due,submission_status=status,last_synced_at=datetime(2026,10,9),source_timezone='Asia/Shanghai'))
            db.commit()
        def override_db():
            with Session(self.engine) as db:
                yield db
        app = FastAPI()
        app.include_router(router, prefix='/api')
        app.dependency_overrides[get_db] = override_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.engine.dispose()

    async def get(self, params=None, token='one'):
        headers = {'Cookie':f'session_id={token}'} if token else {}
        return await self.client.get('/api/assignments', params=params, headers=headers)

    async def test_authentication_required(self):
        for token in (None, 'invalid', 'expired'):
            self.assertEqual((await self.get(token=token)).status_code,401)

    async def test_user_isolation_and_course_scope(self):
        body = (await self.get({'user_id':2})).json()
        self.assertEqual(body['total'],4)
        self.assertTrue(all(x['course_id']==1 for x in body['items']))
        self.assertEqual((await self.get({'course_id':2})).json()['total'],0)
        self.assertEqual((await self.get(token='two')).json()['total'],1)

    async def test_inclusive_beijing_dates(self):
        response = await self.get({'due_from':'2026-10-13','due_to':'2026-10-13'})
        self.assertEqual(response.status_code,200)
        body = response.json()
        self.assertEqual([x['id'] for x in body['items']],[1,2])
        self.assertEqual(body['items'][0]['due_at'],'2026-10-13T00:00:00+08:00')
        self.assertEqual(body['timezone'],'Asia/Shanghai')

    async def test_submission_categories(self):
        for status, ids in [('pending',[1,3]),('submitted',[2]),('unknown',[4])]:
            self.assertEqual([x['id'] for x in (await self.get({'status':status})).json()['items']],ids)

    async def test_stable_pagination_null_deadline_last(self):
        body = (await self.get({'limit':2,'offset':2})).json()
        self.assertEqual(body['total'],4)
        self.assertEqual([x['id'] for x in body['items']],[3,4])
        self.assertIsNone(body['items'][-1]['due_at'])
        self.assertEqual((await self.get({'offset':99})).json()['items'],[])

    async def test_bad_parameters(self):
        for params in ({'due_from':'2026-10-14','due_to':'2026-10-13'}, {'due_to':'9999-12-31'}, {'due_from':'bad'}, {'limit':101}, {'offset':-1}, {'course_id':0}, {'status':'done'}):
            self.assertEqual((await self.get(params)).status_code,422,params)

    async def test_combined_filters_and_source(self):
        body = (await self.get({'course_id':1,'status':'pending','due_to':'2026-10-13'})).json()
        self.assertEqual(body['total'],1)
        self.assertEqual(body['items'][0]['title'],'lab1')
        self.assertIn('source_url',body['items'][0])
        self.assertIn('last_synced_at',body['items'][0])


if __name__ == '__main__':
    unittest.main()
