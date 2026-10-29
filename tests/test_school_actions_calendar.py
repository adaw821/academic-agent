"""Authenticated action routing and calendar boundaries with external calls mocked."""
from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage
from pydantic import ValidationError
import test_school_qa as fixture
from app.models.deadline_reminder import DeadlineReminder
from app.models.course_timetable import CourseSchedule
from app.models.assignments import Assignment
from app.services.teaching_calendar import TeachingCalendar, calendar_match
from app.services.school_qa import query_assignments


class SchoolActionsCalendarTests(unittest.TestCase):
    setUp = fixture.SchoolQATests.setUp
    tearDown = fixture.SchoolQATests.tearDown
    plan = fixture.SchoolQATests.plan

    def action(self, name, args, **extra):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[dict(name=name, args=args, id='action')])
        return self.client.post('/api/projects/1/ask', json=dict(
            query='请执行这项操作', use_school_tools=True, allow_school_actions=True, **extra))

    def test_actions_are_opt_in_and_do_not_accept_identity_or_destination(self):
        with patch('app.services.school_downloads.download_resource') as download:
            self.planner.invoke.return_value = AIMessage(content='', tool_calls=[dict(
                name='download_school_resource', args=dict(course_id=1, resource_id=1), id='action')])
            response = self.client.post('/api/projects/1/ask', json=dict(query='下载附件', use_school_tools=True))
            self.assertEqual(response.status_code, 502)
            for args in [dict(course_id=1, resource_id=1, user_id=2), dict(course_id=True, resource_id=1)]:
                self.assertEqual(self.action('download_school_resource', args).status_code, 502)
            download.assert_not_called()
        with self.sessions() as db:
            with self.assertRaises(Exception) as raised:
                query_assignments(db, 1, dict(name='download_school_resource', args={}))
            self.assertEqual(raised.exception.status_code, 403)

    def test_download_and_import_route_to_authenticated_current_project(self):
        with patch('app.services.school_downloads.download_resource', return_value=dict(resource_id=5, downloaded=True)) as run:
            response = self.action('download_school_resource', dict(course_id=1, resource_id=5))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(run.call_args.args[1:], (1, 1, 5))
        with patch('app.services.school_imports.import_resource', return_value=dict(document_id=4, job_id='job', status='queued')) as run:
            response = self.action('import_school_resource', dict(course_id=1, resource_id=5))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(run.call_args.args[1:], (1, 1, 5, 1))
            self.assertEqual(response.json()['tool_results'][0]['status'], 'queued')

    def test_reminder_read_owner_and_idempotence(self):
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            db.add_all([DeadlineReminder(id=1, assignment_id=1, due_at=now, created_at=now),
                        DeadlineReminder(id=2, assignment_id=3, due_at=now, created_at=now)])
            db.commit()
        self.assertEqual(self.action('mark_school_reminder_read', dict(reminder_id=2)).status_code, 404)
        for _ in range(2):
            self.assertEqual(self.action('mark_school_reminder_read', dict(reminder_id=1)).status_code, 200)
        with self.sessions() as db:
            self.assertIsNotNone(db.get(DeadlineReminder, 1).read_at)
            self.assertIsNone(db.get(DeadlineReminder, 2).read_at)

    def test_calendar_validation_and_date_filtering(self):
        rule = dict(term_name='秋季', term_start='2026-09-01', term_end='2026-12-31',
                    start_week=2, end_week=10, week_pattern='even')
        with self.sessions() as db:
            db.add(CourseSchedule(id=99, course_id=1, day_of_week='Mon', start_time='09:00', end_time='10:00', location='A'))
            db.commit()
        self.assertEqual(self.client.put('/api/timetable/99/calendar', json=rule).status_code, 200)
        for when, expected in [('2026-09-07', True), ('2026-09-14', False), ('2027-01-04', False)]:
            result = self.client.get('/api/timetable', params=dict(course_id=1, on_date=when)).json()
            self.assertEqual(99 in [r['id'] for r in result['items']], expected)
        self.client.cookies.set('session_id', 'token2')
        self.assertEqual(self.client.put('/api/timetable/99/calendar', json=rule).status_code, 404)
        for bad in [dict(week_pattern='odd'), dict(term_start='2026-10-01'),
                    dict(term_start='2026-10-01', term_end='2026-09-01')]:
            with self.assertRaises(ValidationError):
                TeachingCalendar.model_validate(bad)

    def test_action_durable_retry_reuses_checkpoint(self):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[dict(
            name='download_school_resource', args=dict(course_id=1, resource_id=5), id='action')])
        job = self.client.post('/api/projects/1/ask/jobs', json=dict(
            query='下载附件', use_school_tools=True, allow_school_actions=True)).json()['job_id']
        durable = fixture.fixture.durable
        with patch('app.services.school_downloads.download_resource', return_value=dict(resource_id=5, downloaded=True)) as run:
            with patch.object(fixture.rag, 'history_messages_control', side_effect=RuntimeError('save interrupted')):
                durable.run_workflow(job)
            self.assertEqual(self.client.get(f'/api/projects/1/jobs/{job}').json()['status'], 'failed')
            self.assertEqual(self.client.post(f'/api/projects/1/jobs/{job}/retry').status_code, 202)
            durable.run_workflow(job)
            self.assertEqual(self.client.get(f'/api/projects/1/jobs/{job}').json()['status'], 'completed')
            run.assert_called_once()

    def test_assignment_visibility_filter_keeps_submission_status_separate(self):
        with self.sessions() as db:
            db.get(Assignment, 1).source_status = 'not_seen'
            db.get(Assignment, 3).source_status = 'not_seen'
            db.commit()
        self.plan(source_status='not_seen')
        response = self.client.post('/api/projects/1/ask', json=dict(query='哪些作业最近没有看到了', use_school_tools=True))
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()['tool_results'][0]['items']
        self.assertEqual([item['id'] for item in items], [1])
        self.assertEqual(items[0]['status'], 'pending')
        self.assertEqual(items[0]['source_status'], 'not_seen')


if __name__ == '__main__':
    unittest.main()
