"""Read-only live peek: scrape one course's assignments and diff against local.

Reuses test_school_qa's authenticated fixture; the school client and planner are
simulated. The agent loop re-asks the planner once after the read, so the mocked
planner returns the same peek call and the duplicate guard ends the loop.
"""
import unittest
from datetime import datetime
from unittest.mock import patch

from langchain_core.messages import AIMessage

import test_school_qa as fixture
from app.models.courses import Course
from app.services.ispace import CourseSnapshot, SchoolLoginRequired

school_qa = fixture.school_qa
URL = 'https://ispace.bnbu.edu.cn/course/view.php?id=11405'
CHINA = school_qa.CHINA


class SchoolPeekQATests(unittest.TestCase):
    plan = fixture.SchoolQATests.plan  # base setUp calls self.plan(...) at its end.
    tearDown = fixture.SchoolQATests.tearDown

    def setUp(self):
        fixture.SchoolQATests.setUp(self)
        with self.sessions() as db:
            db.get(Course, 1).source_url = URL  # Course 1 must be linked to be peekable.
            db.commit()
        self.patches.extend([
            patch('app.routers.school.load_school_session', return_value='unused-school-session'),
            patch('app.routers.school.ISpaceClient'),
        ])
        self.patches[-2].start()
        self.collect = self.patches[-1].start().return_value.__enter__.return_value.collect_assignments
        # Local Course 1 has external assignments 1 ('Lab 1'), 2 ('Lab 2'), 4 ('Lab 4').
        self.collect.return_value = CourseSnapshot('11405', 'Database Systems', URL, assignments=[
            dict(external_activity_id='1', title='Lab 1',  # unchanged
                 source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id=1',
                 due_at=datetime(2026, 10, 24, 23, 59, tzinfo=CHINA)),
            dict(external_activity_id='2', title='Lab 2 (revised)',  # title changed
                 source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id=2',
                 due_at=datetime(2026, 10, 25, 23, 59, tzinfo=CHINA)),
            dict(external_activity_id='5', title='Lab 5',  # new, not in local
                 source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id=5',
                 due_at=datetime(2026, 11, 1, 23, 59, tzinfo=CHINA)),
        ])  # local '4' is absent from the scrape -> not_seen

    def peek_plan(self, **args):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[
            {'name': 'peek_latest_assignments', 'args': args, 'id': 'peek1'}])

    def ask(self, **kwargs):
        return self.client.post('/api/projects/1/ask', json=dict(
            query='数据库这门课学校上有没有新作业？', use_school_tools=True, **kwargs))

    def test_diff_reports_new_changed_and_not_seen_without_writing(self):
        self.peek_plan(course_id=1)
        data = self.ask().json()['tool_results'][0]
        self.assertEqual(data['tool'], 'peek_latest_assignments')
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['scraped_total'], 3)
        self.assertEqual([a['external_activity_id'] for a in data['new']], ['5'])
        self.assertEqual([a['external_activity_id'] for a in data['changed']], ['2'])
        self.assertEqual([a['external_activity_id'] for a in data['not_seen']], ['4'])
        self.assertEqual((data['new_count'], data['changed_count'], data['not_seen_count']), (1, 1, 1))
        self.collect.assert_called_once()  # scraped once; the loop's re-ask is deduped, not re-run.

    def test_missing_course_asks_for_clarification(self):
        self.peek_plan()  # no course given
        data = self.ask().json()['tool_results'][0]
        self.assertEqual(data['status'], 'clarification_required')
        self.assertEqual(data['reason'], 'course_required')
        self.collect.assert_not_called()

    def test_login_required_surfaces_as_conflict(self):
        self.peek_plan(course_id=1)
        self.collect.side_effect = SchoolLoginRequired('session gone')
        self.assertEqual(self.ask().status_code, 409)


if __name__ == '__main__':
    unittest.main()
