import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from fastapi import FastAPI
import httpx
from sqlalchemy import select, update, func
from sqlalchemy.orm import Session
import test_assignment_queries as fixture
from app.db.database import get_db
from app.models.courses import Course
from app.models.assignments import Assignment
from app.models.course_resources import CourseResource
from app.routers.course_sync import router
from app.services.ispace import CourseSnapshot, SchoolError, SchoolLoginRequired

URL = 'https://ispace.bnbu.edu.cn/course/view.php?id=11405'


class CourseSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.AssignmentQueryTests.asyncSetUp(self)
        await self.client.aclose()
        CourseResource.__table__.create(self.engine)
        with Session(self.engine) as db:
            db.execute(update(Course.__table__).values(source_url=URL))
            db.commit()
        app = FastAPI()
        app.include_router(router, prefix='/api')
        def override_db():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db] = override_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        self.snapshot = CourseSnapshot('11405','Database',URL,assignments=[dict(external_activity_id='1',title='Updated lab',source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id=1',due_at=datetime(2026,10,15,tzinfo=timezone.utc),submission_status='Submitted for grading')])
        self.credentials = patch('app.routers.course_sync.load_school_session',return_value='unused').start()
        self.client_mock = patch('app.routers.course_sync.ISpaceClient').start()
        self.collect = self.client_mock.return_value.__enter__.return_value.collect_course
        self.collect.return_value = self.snapshot

    async def asyncTearDown(self):
        patch.stopall()
        await self.client.aclose()
        self.engine.dispose()

    async def post(self, course=1, token='one'):
        return await self.client.post(f'/api/courses/{course}/sync',headers={'Cookie':f'session_id={token}'} if token else {})

    async def test_auth_and_ownership_before_school_access(self):
        self.assertEqual((await self.post(token=None)).status_code,401)
        self.assertEqual((await self.post(2)).status_code,404)
        self.assertEqual((await self.post(999)).status_code,404)
        self.credentials.assert_not_called()
        self.collect.assert_not_called()

    async def test_refresh_updates_and_is_idempotent(self):
        for _ in range(2):
            response=await self.post()
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['course_id'],1)
        self.credentials.assert_called_with(1)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Assignment.__table__)),5)
            row=db.execute(select(Assignment.__table__).where(Assignment.__table__.c.id==1)).mappings().one()
            self.assertEqual(row['title'],'Updated lab')
            self.assertEqual(row['due_at'].day,15)
            self.assertEqual(db.scalar(select(Course.__table__.c.course_name).where(Course.__table__.c.id==2)),'course2')

    async def test_login_and_upstream_errors_keep_old_data(self):
        for error, status in [(SchoolLoginRequired('expired'),409),(SchoolError('network'),502),(ValueError('bad date'),502)]:
            self.collect.side_effect=error
            self.assertEqual((await self.post()).status_code,status)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(Assignment.__table__.c.title).where(Assignment.__table__.c.id==1)),'lab1')

    async def test_missing_session(self):
        self.credentials.side_effect=SchoolLoginRequired('missing')
        self.assertEqual((await self.post()).status_code,409)
        self.collect.assert_not_called()

    async def test_missing_assignment_is_retained_and_reappearance_restores_visibility(self):
        self.assertEqual((await self.post()).status_code, 200)
        self.snapshot.assignments = []
        self.assertEqual((await self.post()).status_code, 200)
        with Session(self.engine) as db:
            self.assertEqual(db.get(Assignment, 1).source_status, 'not_seen')
            self.assertEqual(db.get(Assignment, 1).title, 'Updated lab')
        self.snapshot.assignments = [dict(external_activity_id='1', title='Returned lab',
            source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id=1')]
        self.assertEqual((await self.post()).status_code, 200)
        with Session(self.engine) as db:
            self.assertEqual(db.get(Assignment, 1).source_status, 'available')

    async def test_unlinked_course(self):
        with Session(self.engine) as db:
            db.execute(update(Course.__table__).where(Course.__table__.c.id==1).values(source_url=None))
            db.commit()
        self.assertEqual((await self.post()).status_code,409)
        self.credentials.assert_not_called()

    async def test_database_failure_rolls_back(self):
        self.snapshot.assignments[0]['title']=None
        self.assertEqual((await self.post()).status_code,500)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(Course.__table__.c.course_name).where(Course.__table__.c.id==1)),'course1')


if __name__=='__main__':
    unittest.main()
