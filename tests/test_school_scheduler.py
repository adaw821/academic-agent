"""Isolated SQLite persistence and OS locks; no real school requests or sleeping."""
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.dict(os.environ, {'DATABASE_URL': 'sqlite://'}):
    from app.db.database import Base
    from app.services import school_scheduler as scheduler
from app.models.assignments import Assignment
from app.models.course_resources import CourseResource
from app.models.courses import Course
from app.models.users import User
from app.services.ispace import CourseSnapshot, SchoolError, SchoolLoginRequired
from scripts.schedule_ispace import main
from sqlalchemy import create_engine, select, update, delete
from sqlalchemy.orm import sessionmaker

NOW = datetime(2026, 10, 25, 4, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=1)


def url(ident):
    return f'https://ispace.bnbu.edu.cn/course/view.php?id={ident}'


def snapshot(source):
    ident = source.rsplit('=', 1)[1]
    return CourseSnapshot(ident, 'Synced course', source, assignments=[dict(
        external_activity_id='lab', title='New lab', source_url=f'https://ispace.bnbu.edu.cn/mod/assign/view.php?id={ident}',
        submission_status='Submitted for grading', due_at=NOW + timedelta(days=1),
    )])


class SchoolSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine('sqlite:///' + (Path(self.temp.name) / 'test.db').as_posix())
        self.addCleanup(self.engine.dispose)
        self.sessions = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine, tables=[User.__table__, Course.__table__, Assignment.__table__, CourseResource.__table__])
        with self.sessions() as db:
            db.execute(User.__table__.insert(), [dict(id=i, username=f'u{i}', email=f'{i}@test', password_hash='unused') for i in (1, 2)])
            for ident, owner, source, synced in [(1, 1, url(1), OLD), (2, 1, url(2), None),
                                                 (3, 2, url(3), OLD), (4, 1, None, None), (5, 1, url(5), NOW)]:
                db.execute(Course.__table__.insert().values(id=ident, user_id=owner, course_name=f'Course {ident}',
                                                           source_url=source, last_synced_at=synced))
            db.execute(Assignment.__table__.insert().values(course_id=1, external_activity_id='lab', title='Old lab',
                source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id=1', last_synced_at=OLD))
            db.commit()
        self.opened = []

        def new_session():
            db = self.sessions()
            self.opened.append(db)
            return db

        def collect(source):
            self.assertTrue(all(not db.in_transaction() for db in self.opened))
            return snapshot(source)

        self.clock = Mock(wraps=datetime)
        self.clock.now.return_value = NOW
        self.client = Mock()
        self.client.collect_course.side_effect = collect
        for item in [patch.object(scheduler, 'SessionLocal', side_effect=new_session),
                     patch.object(scheduler, 'datetime', self.clock),
                     patch('app.services.school_sync.datetime', self.clock),
                     patch.dict(os.environ, {'ISPACE_SESSION_DIR': self.temp.name})]:
            item.start()
            self.addCleanup(item.stop)
        self.credentials = self.enterContext(patch.object(scheduler, 'load_school_session', return_value='secret_cookie'))
        self.factory = self.enterContext(patch.object(scheduler, 'ISpaceClient'))
        self.factory.return_value.__enter__.return_value = self.client

    def batch(self, **kwargs):
        return scheduler.sync_due_courses(1, 3600, **kwargs)

    def title(self, ident):
        with self.sessions() as db:
            return db.scalar(select(Course.__table__.c.course_name).where(Course.__table__.c.id == ident))

    def test_due_courses_only_owner_isolation_and_repeat_skip(self):
        report = self.batch()
        self.assertEqual(report['status'], 'ok')
        self.assertEqual([r['course_id'] for r in report['results']], [1, 2])
        self.assertTrue(all(r['status'] == 'synced' for r in report['results']))
        self.assertEqual(self.title(1), 'Synced course')
        self.assertEqual(self.title(3), 'Course 3')
        self.assertEqual(self.title(4), 'Course 4')
        self.assertEqual(self.title(5), 'Course 5')
        self.credentials.assert_called_once_with(1)
        self.assertEqual(self.batch()['courses_due'], 0)
        self.assertEqual(self.client.collect_course.call_count, 2)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Assignment.__table__.c.title).where(Assignment.__table__.c.course_id == 1)), 'New lab')

    def test_failed_course_keeps_old_data_and_continues(self):
        self.client.collect_course.side_effect = [SchoolError('secret_cookie'), snapshot(url(2))]
        report = self.batch()
        self.assertEqual(report['status'], 'partial_failure')
        self.assertEqual([r['status'] for r in report['results']], ['sync_failed', 'synced'])
        self.assertNotIn('secret_cookie', str(report))
        self.assertEqual(self.title(1), 'Course 1')
        self.assertEqual(self.title(2), 'Synced course')
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Assignment.__table__.c.title).where(Assignment.__table__.c.course_id == 1)), 'Old lab')

    def test_database_failure_rolls_back_course_and_continues(self):
        broken = snapshot(url(1))
        broken.assignments[0]['title'] = None
        self.client.collect_course.side_effect = [broken, snapshot(url(2))]
        report = self.batch()
        self.assertEqual(report['results'][0]['status'], 'sync_failed')
        self.assertEqual(self.title(1), 'Course 1')
        self.assertEqual(self.title(2), 'Synced course')

    def test_missing_and_expired_session_stop_batch_then_reload(self):
        self.credentials.side_effect = SchoolLoginRequired('secret_cookie')
        self.assertEqual(self.batch()['status'], 'school_login_required')
        self.factory.assert_not_called()
        self.credentials.side_effect = None
        self.client.collect_course.side_effect = SchoolLoginRequired('secret_cookie')
        report = self.batch()
        self.assertEqual(report['status'], 'school_login_required')
        self.assertEqual(len(report['results']), 1)
        self.client.collect_course.assert_called_once()
        self.assertEqual(self.title(1), 'Course 1')
        self.client.collect_course.side_effect = snapshot
        self.assertEqual(self.batch()['status'], 'ok')
        self.assertEqual(self.credentials.call_count, 3)

    def test_invalid_source_and_mismatched_snapshot_cannot_create_courses(self):
        with self.sessions() as db:
            db.execute(update(Course.__table__).where(Course.__table__.c.id == 1).values(source_url='https://example.com/course/view.php?id=1'))
            db.commit()
        self.client.collect_course.side_effect = lambda _: snapshot(url(99))
        report = self.batch()
        self.assertEqual([r['status'] for r in report['results']], ['invalid_source', 'sync_failed'])
        self.client.collect_course.assert_called_once_with(url(2))
        self.assertEqual(self.title(2), 'Course 2')
        self.assertIsNone(self.title(99))

    def test_concurrent_refresh_or_deletion_is_not_overwritten(self):
        def change(source):
            with self.sessions() as db:
                if source == url(1):
                    db.execute(update(Course.__table__).where(Course.__table__.c.id == 1).values(
                        course_name='Manual refresh', last_synced_at=NOW))
                else:
                    db.execute(delete(Course.__table__).where(Course.__table__.c.id == 2))
                db.commit()
            return snapshot(source)
        self.client.collect_course.side_effect = change
        report = self.batch()
        self.assertEqual([r['status'] for r in report['results']], ['skipped_changed', 'skipped_changed'])
        self.assertEqual(self.title(1), 'Manual refresh')
        self.assertIsNone(self.title(2))

    def test_shutdown_during_collection_does_not_persist_or_start_next_course(self):
        stop = threading.Event()
        def collect(source):
            stop.set()
            return snapshot(source)
        self.client.collect_course.side_effect = collect
        self.assertEqual(self.batch(stop=stop)['status'], 'stopped')
        self.assertEqual(self.title(1), 'Course 1')
        self.client.collect_course.assert_called_once()

    def test_unknown_user_and_bad_options_do_not_read_credentials(self):
        self.assertEqual(scheduler.sync_due_courses(999, 3600)['status'], 'user_not_found')
        for user_id, seconds in [(0, 3600), (True, 3600), (1, 0), (1, 299), (1, 604801)]:
            with self.assertRaises(ValueError):
                scheduler.sync_due_courses(user_id, seconds)
        self.credentials.assert_not_called()

    def test_os_lock_blocks_second_process_and_releases_after_exception(self):
        code = ('from app.services.school_scheduler import user_sync_lock, SyncAlreadyRunning\n'
                'try:\n with user_sync_lock(1): pass\n'
                'except SyncAlreadyRunning: raise SystemExit(7)\n')
        env = dict(os.environ, DATABASE_URL='sqlite://')
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            with scheduler.user_sync_lock(1):
                result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                                        env=env, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 7, result.stderr.decode(errors='replace'))
                with scheduler.user_sync_lock(2):
                    pass
                raise RuntimeError('interrupted')
        with scheduler.user_sync_lock(1):
            pass

    def test_loop_waits_after_batch_and_recovers_from_batch_failure(self):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        reports = []
        with patch.object(scheduler, 'sync_due_courses', side_effect=[RuntimeError('secret_cookie'), {'status': 'ok'}]) as run:
            self.assertEqual(scheduler.run_schedule(1, 3600, stop=stop, emit=reports.append), 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual([r['status'] for r in reports], ['batch_failed', 'ok'])
        self.assertEqual(stop.wait.call_args_list, [unittest.mock.call(3600)] * 2)
        self.assertNotIn('secret_cookie', str(reports))

    def test_cli_once_exit_codes_validation_and_redacted_errors(self):
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(main(['--user-id', '1', '--once']), 0)
            self.assertIn('"status": "ok"', output.getvalue())
        with patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(main(['--user-id', '999', '--once']), 1)
        with scheduler.user_sync_lock(1), patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(main(['--user-id', '1', '--once']), 2)
        with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as raised:
            main(['--user-id', '1', '--interval-minutes', '0'])
        self.assertEqual(raised.exception.code, 2)
        with (patch.object(scheduler, 'run_schedule', side_effect=RuntimeError('secret_cookie')),
              patch('sys.stderr', new_callable=io.StringIO) as output):
            self.assertEqual(main(['--user-id', '1', '--once']), 1)
            self.assertNotIn('secret_cookie', output.getvalue())


if __name__ == '__main__':
    unittest.main()
