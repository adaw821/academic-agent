"""Authenticated reminder inbox, real SQLite persistence and scheduler integration."""
from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_assignment_queries as fixture
from app.models.assignments import Assignment
from app.models.courses import Course
from app.models.deadline_reminder import DeadlineReminder
from app.services import deadline_reminders as service, school_scheduler as scheduler
from sqlalchemy import select, update, inspect
from sqlalchemy.orm import Session, sessionmaker
from alembic.migration import MigrationContext
from alembic.operations import Operations

NOW = datetime(2026, 10, 25, 4, tzinfo=timezone.utc)
SYNCED = NOW - timedelta(hours=1)


class DeadlineReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.AssignmentQueryTests.asyncSetUp(self)
        DeadlineReminder.__table__.create(self.engine)
        self.clock = Mock(wraps=datetime)
        self.clock.now.return_value = NOW
        self.enterContext(patch.object(service, 'datetime', self.clock))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.dict(os.environ, {'ISPACE_SESSION_DIR': self.temp.name}))
        self.enterContext(patch.object(scheduler, 'SessionLocal', sessionmaker(bind=self.engine)))
        with Session(self.engine) as db:
            db.execute(update(Course.__table__).values(last_synced_at=SYNCED))
            db.execute(update(Assignment.__table__).values(last_synced_at=SYNCED))
            for ident, due in [(1, NOW + timedelta(hours=1)), (2, NOW + timedelta(hours=2)),
                               (3, NOW + timedelta(hours=24)), (4, NOW + timedelta(hours=1)),
                               (5, NOW + timedelta(hours=2))]:
                db.execute(update(Assignment.__table__).where(Assignment.__table__.c.id == ident).values(due_at=due))
            for ident, due, synced in [(6, NOW, SYNCED), (7, NOW + timedelta(hours=24, seconds=1), SYNCED),
                                       (8, None, SYNCED), (9, NOW + timedelta(hours=1), NOW - timedelta(hours=2))]:
                db.execute(Assignment.__table__.insert().values(id=ident, course_id=1, external_activity_id=str(ident),
                    title=f'Lab {ident}', source_url=f'https://ispace.bnbu.edu.cn/mod/assign/view.php?id={ident}',
                    due_at=due, last_synced_at=synced, submission_status='No submissions have been made yet'))
            db.commit()

    asyncTearDown = fixture.AssignmentQueryTests.asyncTearDown

    def generate(self, user_id=1):
        with Session(self.engine) as db:
            count = service.generate_deadline_reminders(db, user_id)
            db.commit()
            return count

    async def get(self, params=None, token='one'):
        return await self.client.get('/api/assignment-reminders', params=params,
            headers={'Cookie': f'session_id={token}'} if token else {})

    async def read(self, ident, token='one'):
        return await self.client.post(f'/api/assignment-reminders/{ident}/read', headers={'Cookie': f'session_id={token}'})

    async def test_horizon_status_freshness_and_user_isolation(self):
        self.assertEqual(self.generate(), 2)
        self.assertEqual(self.generate(2), 1)
        response = await self.get()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual([r['assignment_id'] for r in data['items']], [1, 3])
        self.assertEqual(data['unread_count'], 2)
        self.assertEqual(data['items'][0]['due_at'], '2026-10-25T13:00:00+08:00')
        self.assertEqual([r['assignment_id'] for r in (await self.get(token='two')).json()['items']], [5])
        self.assertEqual((await self.get(token=None)).status_code, 401)
        self.assertEqual((await self.get(token='expired')).status_code, 401)

    async def test_read_is_idempotent_does_not_submit_and_generation_deduplicates(self):
        self.generate()
        ident = (await self.get()).json()['items'][0]['id']
        self.assertEqual((await self.read(ident, token='two')).status_code, 404)
        self.assertEqual((await self.read(999)).status_code, 404)
        self.assertEqual((await self.read(ident)).status_code, 200)
        read_at = (await self.get()).json()['items'][0]['read_at']
        self.clock.now.return_value = NOW + timedelta(microseconds=1)
        self.assertEqual((await self.read(ident)).status_code, 200)
        self.assertEqual((await self.get()).json()['items'][0]['read_at'], read_at)
        self.assertEqual(self.generate(), 0)
        data = (await self.get({'unread_only': True})).json()
        self.assertEqual((data['total'], data['unread_count']), (1, 1))
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(Assignment.__table__.c.submission_status).where(Assignment.__table__.c.id == 1)),
                             'No submissions have been made yet')

    async def test_changed_deadline_makes_new_reminder_and_hides_old_one(self):
        self.generate()
        with Session(self.engine) as db:
            db.execute(update(Assignment.__table__).where(Assignment.__table__.c.id == 1).values(due_at=NOW + timedelta(hours=3)))
            db.commit()
        self.assertEqual(self.generate(), 1)
        self.assertEqual((await self.get()).json()['total'], 2)
        history = (await self.get({'active_only': False})).json()
        self.assertEqual(history['total'], 3)
        self.assertFalse(history['items'][0]['active'])
        self.assertNotEqual(history['items'][0]['due_at'], history['items'][0]['current_due_at'])

    async def test_submitted_unknown_deadline_and_overdue_are_inactive(self):
        self.generate()
        with Session(self.engine) as db:
            db.execute(update(Assignment.__table__).where(Assignment.__table__.c.id == 1).values(submission_status='Submitted for grading'))
            db.execute(update(Assignment.__table__).where(Assignment.__table__.c.id == 3).values(due_at=None))
            db.commit()
        self.assertEqual((await self.get()).json()['total'], 0)
        self.assertTrue(all(not r['active'] for r in (await self.get({'active_only': False})).json()['items']))
        self.assertEqual(self.generate(), 0)
        self.clock.now.return_value = NOW + timedelta(days=2)
        self.assertEqual((await self.get()).json()['total'], 0)

    async def test_stale_or_future_sync_is_not_used(self):
        for synced in (NOW - timedelta(hours=25), NOW + timedelta(hours=1)):
            with Session(self.engine) as db:
                db.execute(update(Course.__table__).values(last_synced_at=synced))
                db.execute(update(Assignment.__table__).values(last_synced_at=synced))
                db.commit()
            self.assertEqual(self.generate(), 0)

    async def test_pagination_validation_and_reads_do_not_generate(self):
        self.assertEqual((await self.get()).json()['total'], 0)
        self.generate()
        data = (await self.get({'limit': 1, 'offset': 1})).json()
        self.assertEqual((data['total'], data['unread_count']), (2, 2))
        self.assertEqual(data['items'][0]['assignment_id'], 3)
        for params in ({'limit': 101}, {'offset': -1}, {'active_only': 'invalid'}):
            self.assertEqual((await self.get(params)).status_code, 422)

    async def test_scheduler_opt_in_and_failed_generation_rolls_back(self):
        reports = []
        def run(enabled):
            with patch.object(scheduler, 'sync_due_courses', return_value={'status': 'ok'}):
                return scheduler.run_schedule(1, 3600, once=True, emit=reports.append, deadline_reminders=enabled)
        self.assertEqual(run(False), 0)
        self.assertEqual((await self.get()).json()['total'], 0)
        original = service.generate_deadline_reminders
        def fail(db, user_id):
            original(db, user_id)
            raise RuntimeError('secret_cookie')
        with patch.object(service, 'generate_deadline_reminders', side_effect=fail):
            self.assertEqual(run(True), 1)
        self.assertEqual((await self.get()).json()['total'], 0)
        self.assertNotIn('secret_cookie', str(reports))
        self.assertEqual(run(True), 0)
        self.assertEqual(reports[-1]['reminders'], {'status': 'ok', 'created': 2})
        self.assertEqual(run(True), 0)
        self.assertEqual(reports[-1]['reminders']['created'], 0)

    async def test_scheduler_skips_expired_session_and_handles_partial_sync(self):
        reports = []
        for status, created in [('school_login_required', 0), ('partial_failure', 2)]:
            with patch.object(scheduler, 'sync_due_courses', return_value={'status': status}):
                scheduler.run_schedule(1, 3600, once=True, emit=reports.append, deadline_reminders=True)
            self.assertEqual((await self.get()).json()['total'], created)
        self.assertEqual(reports[0]['reminders']['status'], 'skipped')

    async def test_migration_upgrade_constraints_and_downgrade(self):
        DeadlineReminder.__table__.drop(self.engine)
        path = Path(__file__).resolve().parents[1] / 'migration/versions/e6a219db503c_deadline_reminders.py'
        spec = importlib.util.spec_from_file_location('reminder_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        self.assertEqual(self.generate(), 2)
        self.assertEqual(self.generate(), 0)
        constraints = inspect(self.engine).get_unique_constraints('deadline_reminders')
        self.assertIn(['assignment_id', 'due_at'], [c['column_names'] for c in constraints])
        with self.engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        self.assertFalse(inspect(self.engine).has_table('deadline_reminders'))


if __name__ == '__main__':
    unittest.main()
