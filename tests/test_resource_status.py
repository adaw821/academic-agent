"""Resource presence is derived from successful sync timestamps, never deletion."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.orm import Session

import test_course_sync as fixture
from app.db.database import get_db
from app.models.courses import Course
from app.models.course_resources import CourseResource
from app.routers import school
from app.services.ispace import SchoolError, SchoolLoginRequired


class ResourceStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.CourseSyncTests.asyncSetUp(self)
        await self.client.aclose()
        app = FastAPI()
        app.include_router(school.router, prefix='/api')
        app.include_router(fixture.router, prefix='/api')
        def override_db():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db] = override_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        self.time = datetime(2026, 10, 24, tzinfo=timezone.utc)
        self.clock = patch('app.services.school_sync.datetime', wraps=datetime).start()
        self.resources = [dict(source_key=str(i), title=f'Lecture {i}', resource_type='file',
                               source_url=f'https://ispace.bnbu.edu.cn/pluginfile.php/12/{i}.pdf') for i in (1, 2)]

    asyncTearDown = fixture.CourseSyncTests.asyncTearDown
    post = fixture.CourseSyncTests.post

    async def sync(self, resources):
        self.time += timedelta(minutes=1)
        self.clock.now.return_value = self.time
        self.snapshot.resources = resources
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)

    async def get(self, params=None, course=1, token='one'):
        return await self.client.get(f'/api/courses/{course}/resources', params=params,
                                     headers={'Cookie': f'session_id={token}'} if token else {})

    async def test_missing_then_reappearing_keeps_id_and_download(self):
        await self.sync(self.resources)
        initial = (await self.get()).json()['items']
        self.assertEqual([row['source_status'] for row in initial], ['seen', 'seen'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cached.pdf'
            path.write_bytes(b'%PDF-cache')
            with Session(self.engine) as db:
                db.execute(update(CourseResource).where(CourseResource.id == initial[0]['id']).values(
                    storage_path=str(path), purpose='lecture', content_type='application/pdf'))
                db.commit()
            await self.sync(self.resources[1:])
            missing = (await self.get({'source_status': 'not_seen'})).json()
            self.assertEqual(missing['total'], 1)
            self.assertEqual(missing['items'][0]['id'], initial[0]['id'])
            self.assertEqual(missing['items'][0]['last_synced_at'], initial[0]['last_synced_at'])
            self.assertNotEqual(missing['items'][0]['source_checked_at'], initial[0]['source_checked_at'])
            self.assertNotIn('storage_path', missing['items'][0])
            self.assertTrue(path.is_file())
            await self.sync(self.resources)
            restored = (await self.get({'source_status': 'seen'})).json()
            self.assertEqual(restored['total'], 2)
            self.assertEqual(restored['items'][0]['id'], initial[0]['id'])
            with Session(self.engine) as db:
                row = db.execute(select(CourseResource.__table__).where(CourseResource.id == initial[0]['id'])).mappings().one()
                self.assertEqual(row['storage_path'], str(path))
                self.assertEqual(row['purpose'], 'lecture')

    async def test_empty_successful_sync_marks_all_not_seen_and_filters_before_paging(self):
        await self.sync(self.resources)
        await self.sync([])
        self.assertEqual((await self.get()).json()['total'], 2)
        self.assertEqual((await self.get({'source_status': 'seen'})).json()['total'], 0)
        response = (await self.get({'source_status': 'not_seen', 'limit': 1, 'offset': 1})).json()
        self.assertEqual(response['total'], 2)
        self.assertEqual(len(response['items']), 1)
        self.assertEqual(response['items'][0]['title'], 'Lecture 2')

    async def test_network_login_and_database_failures_do_not_change_observations(self):
        await self.sync(self.resources)
        before = (await self.get()).json()
        for error, code in [(SchoolError('partial folder failure'), 502), (SchoolLoginRequired('expired'), 409)]:
            self.collect.side_effect = error
            self.assertEqual((await self.post()).status_code, code)
            self.assertEqual((await self.get()).json(), before)
        self.collect.side_effect = None
        self.snapshot.resources = [dict(self.resources[0], title=None)]
        self.time += timedelta(minutes=1)
        self.clock.now.return_value = self.time
        self.assertEqual((await self.post()).status_code, 500)
        self.assertEqual((await self.get()).json(), before)

    async def test_unknown_when_course_timestamp_missing_or_inconsistent(self):
        await self.sync(self.resources)
        for stamp in (None, self.time - timedelta(days=1)):
            with Session(self.engine) as db:
                db.execute(update(Course).where(Course.id == 1).values(last_synced_at=stamp))
                db.commit()
            result = (await self.get({'source_status': 'unknown'})).json()
            self.assertEqual(result['total'], 2)
            self.assertTrue(all(row['source_status'] == 'unknown' for row in result['items']))

    async def test_authentication_owner_isolation_and_invalid_filter(self):
        await self.sync(self.resources)
        self.assertEqual((await self.get(token=None)).status_code, 401)
        self.assertEqual((await self.get(token='two')).status_code, 404)
        self.assertEqual((await self.get(course=2)).status_code, 404)
        self.assertEqual((await self.get({'source_status': 'deleted'})).status_code, 422)
        with Session(self.engine) as db:
            db.execute(CourseResource.__table__.insert().values(
                course_id=2, source_key='other', title='other user', resource_type='file',
                source_url='https://ispace.bnbu.edu.cn/pluginfile.php/12/other.pdf', last_synced_at=self.time))
            db.commit()
        await self.sync([])
        other = (await self.get(course=2, token='two')).json()
        self.assertEqual(other['items'][0]['source_status'], 'unknown')
        self.assertEqual(other['total'], 1)


if __name__ == '__main__':
    unittest.main()
