import os
from pathlib import Path
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DATABASE_URL', 'sqlite://')
import httpx
from sqlalchemy import create_engine, select, func, event
from sqlalchemy.orm import Session
from app.services.ispace import ISpaceClient, SchoolError, SchoolLoginRequired, parse_course, parse_date, parse_assignment, parse_folder, ORIGIN
from app.services.school_sync import sync_course
from app.models.users import User
from app.models.courses import Course
from app.models.assignments import Assignment
from app.models.course_resources import CourseResource

URL = ORIGIN + '/course/view.php?id=11405'
COURSE = '''<h1>Database Management Systems</h1><div id="region-main"><div class="course-content"><li class="activity" id="module-387779"><a href="/mod/assign/view.php?id=387779"><span>lab5<span class="accesshide"> Assignment</span></span></a><button>Done</button><div data-region="activity-dates"><div><strong>Opened:</strong> Thursday, 8 October 2026, 12:00 AM</div><div><strong>Due:</strong> Tuesday, 13 October 2026, 12:00 AM</div></div><a href="/pluginfile.php/123/mod_assign/intro/questions.pdf">Requirement</a></li><li class="activity"><a href="/mod/page/view.php?id=356600">Lab</a></li></div></div>'''
DETAIL = '''<div id="region-main"><h2>lab5</h2><div data-region="activity-dates"><div><strong>Due:</strong> Tuesday, 13 October 2026, 12:00 AM</div></div><div id="intro">Solve questions<a href="/pluginfile.php/123/mod_assign/intro/questions.pdf">Requirement</a></div><table><tr><th>Submission status</th><td>Submitted for grading</td></tr><tr><th>Grading status</th><td>Not graded</td></tr></table><a href="/pluginfile.php/123/assignsubmission_file/submission_files/1/answer.txt">My answer</a></div>'''


class CollectionTests(unittest.TestCase):
    def test_real_page_shape_and_midnight(self):
        snapshot = parse_course(COURSE, URL)
        assignment = snapshot.assignments[0]
        self.assertEqual(assignment['title'], 'lab5')
        self.assertEqual(assignment['due_at'].isoformat(), '2026-10-13T00:00:00+08:00')
        self.assertNotIn('submission_status', assignment)
        self.assertEqual(parse_date('Friday, 9 October 2026, 12:00 PM').hour, 12)
        self.assertEqual(parse_date('2026年10月13日 00:00').hour, 0)
        detail, files = parse_assignment(DETAIL)
        self.assertEqual(detail['submission_status'], 'Submitted for grading')
        self.assertEqual(len(files), 1)
        self.assertNotIn('answer.txt', files[0]['source_url'])

    def test_invalid_date_and_login_do_not_look_empty(self):
        with self.assertRaises(SchoolError):
            parse_course(COURSE.replace('Tuesday, 13 October 2026, 12:00 AM', 'tomorrow'), URL)
        with self.assertRaises(SchoolLoginRequired):
            parse_course('<input name="password">', URL)
        with self.assertRaises(SchoolError):
            parse_course('<h1>Access denied</h1>', URL)

    def test_collect_details_and_page_resources(self):
        seen = []
        def handler(request):
            seen.append(request.url.path)
            self.assertEqual(request.headers['cookie'], 'MoodleSession=test')
            html = {'/course/view.php': COURSE, '/mod/assign/view.php': DETAIL,
                    '/mod/page/view.php': '<div id="region-main"><a href="/pluginfile.php/12/mod_page/content/1/lab.pdf">Lab questions</a></div>'}[request.url.path]
            return httpx.Response(200, text=html, headers={'content-type':'text/html'})
        with ISpaceClient('test', transport=httpx.MockTransport(handler)) as client:
            snapshot = client.collect_course(URL)
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(snapshot.resources), 3)
        self.assertEqual(snapshot.assignments[0]['submission_status'], 'Submitted for grading')

    def test_redirect_does_not_leak_session_to_other_hosts(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={'location': 'https://other.example/login'})
        with ISpaceClient('test', transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(SchoolError):
                client.collect_course(URL)
        self.assertEqual(len(calls), 1)
        with ISpaceClient('test', transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={'location':'/login/index.php'}))) as client:
            with self.assertRaises(SchoolLoginRequired):
                client.collect_course(URL)

    def test_dynamic_directory_is_explicitly_incomplete(self):
        with ISpaceClient('test', transport=httpx.MockTransport(lambda r: httpx.Response(200, text='<div></div>', headers={'content-type':'text/html'}))) as client:
            self.assertFalse(client.discover_courses()['complete'])

    def test_sync_isolation_update_and_rollback(self):
        engine = create_engine('sqlite://')
        @event.listens_for(engine, 'connect')
        def enable_fk(conn, _):
            conn.execute('PRAGMA foreign_keys=ON')
        tables = [User.__table__, Course.__table__, Assignment.__table__, CourseResource.__table__]
        User.metadata.create_all(engine, tables=tables)
        with Session(engine) as db:
            db.execute(User.__table__.insert(), [dict(id=n, username=f'u{n}', email=f'u{n}@test', password_hash='unused') for n in (1,2)])
            db.commit()
            snapshot = parse_course(COURSE, URL)
            first = sync_course(db, 1, snapshot)
            sync_course(db, 1, snapshot)
            second = sync_course(db, 2, snapshot)
            self.assertNotEqual(first['course_id'], second['course_id'])
            self.assertEqual(db.scalar(select(func.count()).select_from(Assignment.__table__)), 2)
            snapshot.assignments[0]['due_at'] = parse_date('Wednesday, 14 October 2026, 12:00 AM')
            sync_course(db, 1, snapshot)
            due = db.scalar(select(Assignment.__table__.c.due_at).where(Assignment.__table__.c.course_id == first['course_id']))
            self.assertEqual(due.day, 14)
            other_due = db.scalar(select(Assignment.__table__.c.due_at).where(Assignment.__table__.c.course_id == second['course_id']))
            self.assertEqual(other_due.day, 13)
            snapshot.title = 'should roll back'
            snapshot.resources[0]['title'] = None
            with self.assertRaises(Exception):
                sync_course(db, 1, snapshot)
            self.assertEqual(db.scalar(select(Course.__table__.c.course_name).where(Course.__table__.c.id == first['course_id'])), 'Database Management Systems')
        engine.dispose()


class FolderTests(unittest.TestCase):
    FOLDER = ORIGIN + '/mod/folder/view.php?id=10'
    FILE = '/pluginfile.php/12/mod_folder/content/0/week1/lecture.pdf'

    def folder_html(self, links=''):
        return '<div id="region-main"><div class="foldertree"><ul>' + links + '</ul></div></div>'

    def test_nested_collapsed_files_and_safe_deduplication(self):
        html = self.folder_html(f'''
          <li>Week 1<ul style="display:none"><li><a href="{self.FILE}?forcedownload=1">Lecture</a></li>
          <li><a href="/pluginfile.php/12/mod_folder/content/0/week1/lab.txt">Lab</a></li></ul></li>
          <li><a href="{self.FILE}">Duplicate</a></li>
          <li><a href="https://other.example/file.pdf">External</a></li>
          <li><a href="/pluginfile.php/12/assignsubmission_file/submission_files/1/answer.txt">Student</a></li>
          <li><a href="/mod/folder/download_folder.php?id=10">Download all</a></li>
          <li><a href="/mod/folder/edit.php?id=10">Edit</a></li>''')
        html += '<a href="/pluginfile.php/12/unrelated.pdf">Outside tree</a>'
        files = parse_folder(html, self.FOLDER)
        self.assertEqual(len(files), 2)
        self.assertTrue(all(item['resource_type'] == 'file' for item in files))
        self.assertEqual(files[0]['source_url'], ORIGIN + self.FILE)

    def test_empty_tree_is_valid_but_missing_tree_or_login_fails(self):
        self.assertEqual(parse_folder(self.folder_html(), self.FOLDER), [])
        for html, error in [('<div id="region-main">Access denied</div>', SchoolError),
                            ('<input name="password">', SchoolLoginRequired),
                            ('<div class="errorbox">Error</div>', SchoolError)]:
            with self.subTest(html=html), self.assertRaises(error):
                parse_folder(html, self.FOLDER)

    def collect(self, folder_pages, *, course_links='', page_links=''):
        self.seen = []
        def handler(request):
            self.seen.append(str(request.url))
            self.assertEqual(request.headers['cookie'], 'MoodleSession=test')
            if request.url.path == '/course/view.php':
                html = '<h1>Course</h1><div id="region-main"><div class="course-content">' + course_links + '</div></div>'
            elif request.url.path == '/mod/page/view.php':
                html = '<div id="region-main">' + page_links + '</div>'
            else:
                html = folder_pages[request.url.params['id']]
            return httpx.Response(200, text=html, headers={'content-type': 'text/html'})
        with ISpaceClient('test', transport=httpx.MockTransport(handler)) as client:
            return client.collect_course(URL)

    def test_course_and_page_folders_cycle_dedup_and_resource_persistence(self):
        folder_link = '<a href="/mod/folder/view.php?id=10">Notes</a>'
        snapshot = self.collect({
            '10': self.folder_html(f'<a href="{self.FILE}">Lecture</a><a href="/mod/folder/view.php?id=11">More</a>'),
            '11': self.folder_html(folder_link + f'<a href="{self.FILE}?forcedownload=1">Same file</a>'),
        }, course_links=folder_link + '<a href="/mod/page/view.php?id=20">Page</a>', page_links=folder_link)
        self.assertEqual(len(self.seen), 4)
        files = [item for item in snapshot.resources if item['resource_type'] == 'file']
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['source_url'], ORIGIN + self.FILE)
        engine = create_engine('sqlite://')
        try:
            User.metadata.create_all(engine, tables=[User.__table__, Course.__table__, Assignment.__table__, CourseResource.__table__])
            with Session(engine) as db:
                db.execute(User.__table__.insert().values(id=1, username='one', email='one@test', password_hash='unused'))
                db.commit()
                sync_course(db, 1, snapshot)
                sync_course(db, 1, snapshot)
                rows = db.execute(select(CourseResource.__table__).where(CourseResource.resource_type == 'file')).mappings().all()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['source_url'], ORIGIN + self.FILE)
        finally:
            engine.dispose()

    def test_folder_discovered_only_in_page_is_expanded(self):
        snapshot = self.collect({'10': self.folder_html(f'<a href="{self.FILE}">Lecture</a>')},
                                course_links='<a href="/mod/page/view.php?id=20">Page</a>',
                                page_links='<a href="/mod/folder/view.php?id=10">Folder</a>')
        self.assertTrue(any(item['source_url'] == ORIGIN + self.FILE for item in snapshot.resources))
        self.assertEqual(len(self.seen), 3)

    def test_folder_and_resource_limits_fail_without_partial_snapshot(self):
        link = '<a href="/mod/folder/view.php?id=10">Folder</a>'
        with patch('app.services.ispace.MAX_FOLDERS', 1):
            with self.assertRaisesRegex(SchoolError, 'Folder 数量'):
                self.collect({'10': self.folder_html('<a href="/mod/folder/view.php?id=11">Next</a>')}, course_links=link)
            self.assertEqual(len(self.seen), 2)
        with patch('app.services.ispace.MAX_FOLDER_RESOURCES', 1):
            with self.assertRaisesRegex(SchoolError, 'Folder 资源数量'):
                self.collect({'10': self.folder_html(f'<a href="{self.FILE}">One</a><a href="/pluginfile.php/12/two.txt">Two</a>')}, course_links=link)

    def test_folder_fetch_errors_abort_collection(self):
        for response, error in [(httpx.Response(403), SchoolLoginRequired),
                                (httpx.Response(500), SchoolError),
                                (httpx.Response(302, headers={'location': 'https://other.example/folder'}), SchoolError)]:
            calls = []
            def handler(request):
                calls.append(str(request.url))
                if request.url.path == '/course/view.php':
                    return httpx.Response(200, text='<h1>Course</h1><div id="region-main"><div class="course-content"><a href="/mod/folder/view.php?id=10">Folder</a></div></div>', headers={'content-type': 'text/html'})
                return response
            with self.subTest(status=response.status_code):
                with ISpaceClient('test', transport=httpx.MockTransport(handler)) as client:
                    with self.assertRaises(error):
                        client.collect_course(URL)
                self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
