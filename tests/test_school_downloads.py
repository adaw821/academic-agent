"""Real authenticated routes, temporary files and mocked school HTTP only."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.orm import Session

import test_assignment_queries as fixture
from app.db.database import get_db
from app.models.course_resources import CourseResource
from app.routers.school import router
from app.services import school_downloads as downloads
from app.services.ispace import ISpaceClient, ORIGIN, SchoolLoginRequired

FILE = '/pluginfile.php/12/mod_resource/content/1/lecture.pdf'


class SchoolDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.AssignmentQueryTests.asyncSetUp(self)
        await self.client.aclose()
        CourseResource.__table__.create(self.engine)
        with Session(self.engine) as db:
            db.execute(CourseResource.__table__.insert().values(
                id=1, course_id=1, source_key='file', title='Lecture', resource_type='resource',
                source_url=ORIGIN + '/mod/resource/view.php?id=9', last_synced_at=datetime.now(timezone.utc)))
            db.commit()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'downloads'
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(downloads, 'DOWNLOAD_ROOT', self.root).start()
        self.credentials = patch.object(downloads, 'load_school_session', return_value='test').start()
        self.requests = []
        self.handler = self.normal_response
        def handle(request):
            self.requests.append(request)
            self.assertEqual(request.headers['cookie'], 'MoodleSession=test')
            return self.handler(request)
        self.transport = httpx.MockTransport(handle)
        patch.object(downloads, 'ISpaceClient', side_effect=lambda cookie: ISpaceClient(cookie, transport=self.transport)).start()
        app = FastAPI()
        app.include_router(router, prefix='/api')
        def override_db():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db] = override_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.engine.dispose()

    def normal_response(self, request):
        if request.url.path == '/mod/resource/view.php':
            return httpx.Response(302, headers={'location': FILE})
        return httpx.Response(200, content=b'%PDF-1.7 test', headers={'content-type': 'application/pdf'})

    async def call(self, method='POST', course=1, resource=1, token='one'):
        return await self.client.request(method, f'/api/courses/{course}/resources/{resource}/download',
                                         headers={'Cookie': f'session_id={token}'} if token else {})

    def row(self):
        with Session(self.engine) as db:
            return db.execute(select(CourseResource.__table__)).mappings().one()

    def change(self, **values):
        with Session(self.engine) as db:
            db.execute(update(CourseResource.__table__).values(**values))
            db.commit()

    def assert_no_saved_file(self):
        self.assertIsNone(self.row()['storage_path'])
        self.assertFalse(any(path.is_file() for path in self.root.rglob('*')))

    async def test_auth_and_ownership_before_network(self):
        for method in ('POST', 'GET'):
            for kwargs, expected in [({'token': None}, 401), ({'token': 'expired'}, 401),
                                     ({'token': 'two'}, 404), ({'course': 2}, 404), ({'resource': 99}, 404)]:
                self.assertEqual((await self.call(method, **kwargs)).status_code, expected)
        self.credentials.assert_not_called()
        self.assertEqual(self.requests, [])

    async def test_download_status_cache_and_missing_file_repair(self):
        self.assertFalse((await self.call('GET')).json()['downloaded'])
        response = await self.call()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['cached'])
        path = Path(self.row()['storage_path'])
        self.assertEqual(path.read_bytes(), b'%PDF-1.7 test')
        self.assertEqual(path.suffix, '.pdf')
        self.assertTrue((await self.call()).json()['cached'])
        self.assertEqual(len(self.requests), 2)
        body = (await self.call('GET')).json()
        self.assertTrue(body['downloaded'])
        self.assertNotIn('storage_path', body)
        path.unlink()
        self.assertFalse((await self.call('GET')).json()['downloaded'])
        self.assertFalse((await self.call()).json()['cached'])

    async def test_embedded_resource_and_direct_file(self):
        normal = self.normal_response
        self.handler = lambda request: (httpx.Response(200, text=f'<object data="{FILE}"></object>',
                                                      headers={'content-type': 'text/html'})
                                        if request.url.path == '/mod/resource/view.php' else normal(request))
        self.assertEqual((await self.call()).status_code, 200)
        Path(self.row()['storage_path']).unlink()
        self.change(source_url=ORIGIN + FILE, resource_type='file')
        self.assertEqual((await self.call()).status_code, 200)

    async def test_reject_redirect_login_html_empty_and_unsupported_files(self):
        cases = [
            (httpx.Response(302, headers={'location': 'https://other.example/file.pdf'}), 502),
            (httpx.Response(302, headers={'location': '/login/index.php'}), 409),
            (httpx.Response(200, text='<input name="password">', headers={'content-type': 'text/html'}), 409),
            (httpx.Response(200, content=b'<html>login</html>'), 502),
            (httpx.Response(200, content=b''), 502),
            (httpx.Response(200, content=b'bad', headers={'content-disposition': 'attachment; filename="bad.exe"'}), 502),
            (httpx.Response(200, content=b'x', headers={'content-length': str(51 * 1024 * 1024)}), 502),
        ]
        self.change(source_url=ORIGIN + FILE, resource_type='file')
        for response, status in cases:
            with self.subTest(status=status, headers=response.headers):
                self.requests.clear()
                self.handler = lambda request: response
                self.assertEqual((await self.call()).status_code, status)
                self.assertEqual(len(self.requests), 1)
                self.assert_no_saved_file()

    async def test_stream_limit_and_interruption_cleanup(self):
        self.change(source_url=ORIGIN + FILE, resource_type='file')
        original = ISpaceClient.download_resource
        with patch.object(ISpaceClient, 'download_resource', lambda client, url, destination:
                          original(client, url, destination, max_bytes=4)):
            self.handler = lambda request: httpx.Response(200, stream=httpx.ByteStream(b'too large'))
            self.assertEqual((await self.call()).status_code, 502)
        self.assert_no_saved_file()
        class BrokenStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'%PDF-' * 14000
                raise httpx.ReadError('interrupted')
        self.handler = lambda request: httpx.Response(200, stream=BrokenStream())
        self.assertEqual((await self.call()).status_code, 502)
        self.assert_no_saved_file()

    async def test_commit_failure_cleans_new_file(self):
        with patch.object(Session, 'commit', side_effect=RuntimeError('database unavailable')):
            self.assertEqual((await self.call()).status_code, 500)
        self.assert_no_saved_file()

    async def test_session_missing_and_resource_type_rejected(self):
        self.credentials.side_effect = SchoolLoginRequired('missing')
        self.assertEqual((await self.call()).status_code, 409)
        self.assert_no_saved_file()
        for values in ({'resource_type': 'folder'}, {'resource_type': 'file', 'purpose': 'student_submission'}):
            self.change(**values)
            self.assertEqual((await self.call()).status_code, 422)
        self.assertEqual(self.requests, [])

    async def test_saved_path_cannot_escape_resource_directory(self):
        self.change(storage_path=str(Path(self.temp.name) / 'outside.pdf'))
        self.assertEqual((await self.call()).status_code, 409)
        self.assertEqual((await self.call('GET')).status_code, 409)
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
