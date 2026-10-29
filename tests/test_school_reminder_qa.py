"""Real QA graph and reminder database queries; only model output is simulated."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch
from langchain_core.messages import AIMessage
from sqlalchemy import delete, func, select, update

import test_school_qa as fixture
from app.models.assignments import Assignment
from app.models.courses import Course
from app.models.deadline_reminder import DeadlineReminder
from app.models.message import Message
from app.services import deadline_reminders


class SchoolReminderQATests(unittest.TestCase):
    def setUp(self):
        fixture.SchoolQATests.setUp(self)
        self.now = self.clock.now.return_value.astimezone(timezone.utc)
        clock = Mock(wraps=datetime)
        clock.now.return_value = self.now
        self.patches.append(patch.object(deadline_reminders, 'datetime', clock))
        self.patches[-1].start()
        with self.sessions() as db:
            synced = self.now - timedelta(minutes=30)
            db.execute(update(Course).values(last_synced_at=synced))
            for ident in (1, 2, 3, 4):
                due = self.now + timedelta(hours=ident)
                db.execute(update(Assignment).where(Assignment.id == ident).values(due_at=due, last_synced_at=synced))
                db.add(DeadlineReminder(id=ident, assignment_id=ident, due_at=due,
                    created_at=self.now - timedelta(minutes=10), read_at=self.now - timedelta(minutes=5) if ident == 4 else None))
            # An eligible assignment without a generated reminder must remain absent from the inbox.
            db.add(Assignment(id=5, course_id=1, external_activity_id='5', title='No reminder yet',
                source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id=5',
                due_at=self.now + timedelta(hours=5), last_synced_at=synced, submission_status='No submissions have been made yet'))
            db.commit()
        self.reminder_plan()

    plan = fixture.SchoolQATests.plan
    tearDown = fixture.SchoolQATests.tearDown

    def reminder_plan(self, **args):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[
            {'name': 'query_deadline_reminders', 'args': args, 'id': 'reminder-call'}])

    def ask(self, **kwargs):
        return self.client.post('/api/projects/1/ask', json={
            'query': '我的站内截止提醒有哪些？', 'use_school_tools': True, **kwargs})

    def test_active_inbox_is_user_scoped_read_only_and_persisted(self):
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        data = result['tool_results'][0]
        self.assertEqual(data['tool'], 'query_deadline_reminders')
        self.assertEqual([r['assignment_id'] for r in data['items']], [1, 4])
        self.assertEqual((data['total'], data['unread_count']), (2, 1))
        self.assertEqual(data['items'][0]['due_at'], '2026-10-25T13:00:00+08:00')
        self.assertIn(deadline_reminders.WARNING, result['answer'])
        self.assertEqual(result['sources'], [])
        fixture.rag.retrieve_documents.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(DeadlineReminder)), 4)
            self.assertIsNone(db.get(DeadlineReminder, 1).read_at)
            saved = db.scalar(select(Message).where(Message.conversation_id == result['conversation_id'], Message.role == 'assistant'))
            self.assertEqual(saved.response_data['tool_results'][0]['unread_count'], 1)

    def test_unread_history_and_pagination_keep_distinct_meanings(self):
        self.reminder_plan(unread_only=True)
        self.assertEqual([r['id'] for r in self.ask().json()['tool_results'][0]['items']], [1])
        self.reminder_plan(active_only=False, limit=1, offset=1)
        data = self.ask().json()['tool_results'][0]
        self.assertEqual((data['total'], data['unread_count'], data['has_more']), (3, 2, True))
        self.assertEqual(data['items'][0]['id'], 2)
        self.assertFalse(data['items'][0]['active'])  # Submitted assignment, historical reminder.
        self.reminder_plan(active_only=False, unread_only=True)
        self.assertEqual([r['id'] for r in self.ask().json()['tool_results'][0]['items']], [1, 2])

    def test_empty_inbox_does_not_generate_reminders(self):
        with self.sessions() as db:
            db.execute(delete(DeadlineReminder).where(DeadlineReminder.id.in_([1, 2, 4])))
            db.commit()
        data = self.ask().json()['tool_results'][0]
        self.assertEqual((data['items'], data['total'], data['unread_count']), ([], 0, 0))
        self.assertIn('不能据此判断没有待交作业', data['coverage_warning'])
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(DeadlineReminder)), 1)

    def test_invalid_parameters_and_write_tools_are_rejected(self):
        for args in ({'user_id': 2}, {'project_id': 2}, {'active_only': 'false'},
                     {'unread_only': 1}, {'limit': 21}, {'offset': -1}, {'mark_read': True}):
            self.reminder_plan(**args)
            self.assertEqual(self.ask().status_code, 502, args)
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[
            {'name': 'mark_reminder_read', 'args': {'reminder_id': 1}, 'id': 'forbidden'}])
        self.assertEqual(self.ask().status_code, 502)
        self.llm.invoke.assert_not_called()
        with self.sessions() as db:
            self.assertIsNone(db.get(DeadlineReminder, 1).read_at)
            self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 2)

    def test_document_scope_and_project_auth_keep_existing_priority(self):
        self.assertEqual(self.client.post('/api/projects/2/ask', json={
            'query': '提醒', 'use_school_tools': True}).status_code, 403)
        self.assertEqual(self.ask(doc_ids=[1]).json()['answer'], 'document answer')
        self.planner.invoke.assert_not_called()

    def test_durable_retry_replays_serialized_reminder_snapshot(self):
        durable = fixture.fixture.durable
        job = self.client.post('/api/projects/1/ask/jobs', json={
            'query': '我的截止提醒', 'use_school_tools': True}).json()
        with patch.object(durable.Checkpoints, 'finish', side_effect=RuntimeError('interrupted')):
            durable.run_workflow(job['job_id'])
        self.assertEqual(self.client.post(f"/api/projects/1/jobs/{job['job_id']}/retry").status_code, 202)
        durable.run_workflow(job['job_id'])
        self.planner.invoke.assert_called_once()
        self.llm.invoke.assert_called_once()
        result = self.client.get(f"/api/projects/1/jobs/{job['job_id']}").json()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result']['tool_results'][0]['items'][0]['due_at'], '2026-10-25T13:00:00+08:00')
        with self.sessions() as db:
            self.assertIsNone(db.get(DeadlineReminder, 1).read_at)


if __name__ == '__main__':
    unittest.main()
