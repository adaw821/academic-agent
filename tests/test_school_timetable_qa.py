"""Weekly timetable queries through authenticated QA; model output is simulated."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage
from sqlalchemy import delete, func, select

import test_school_qa as fixture
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.models.message import Message
from app.services.school_timetable_qa import WARNING


class SchoolTimetableQATests(unittest.TestCase):
    def setUp(self):
        fixture.SchoolQATests.setUp(self)
        with self.sessions() as db:
            db.execute(delete(CourseSchedule))
            db.get(Course, 1).teachers = 'Dr. Chen'
            db.add(Course(id=3, user_id=1, course_name='Database Design'))
            db.flush()
            for ident, course, day, start in [(11, 1, 'Mon', '10:00'), (12, 1, 'Monday', '9:00'),
                                             (13, 1, '星期一', '08:00'), (14, 1, 'Tue', '11:00'),
                                             (15, 2, 'Mon', '07:00'), (16, 1, 'Sun', '13:00'),
                                             (17, 1, 'unrecognized', '14:00'), (18, 3, 'Mon', '15:00')]:
                db.add(CourseSchedule(id=ident, course_id=course, day_of_week=day, start_time=start,
                                      end_time='16:00', location=f'Room {ident}'))
            db.commit()
        self.timetable_plan(weekday='Mon')

    plan = fixture.SchoolQATests.plan
    tearDown = fixture.SchoolQATests.tearDown

    def timetable_plan(self, **args):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[
            {'name': 'query_timetable', 'args': args, 'id': 'timetable-call'}])

    def ask(self, **kwargs):
        return self.client.post('/api/projects/1/ask', json={
            'query': '周一几点上课，在哪上？', 'use_school_tools': True, **kwargs})

    def test_owner_scope_weekday_aliases_order_and_saved_result(self):
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        data = result['tool_results'][0]
        self.assertEqual(data['tool'], 'query_timetable')
        self.assertEqual([r['id'] for r in data['items']], [13, 12, 11, 18])
        self.assertEqual(data['items'][0]['teachers'], 'Dr. Chen')
        self.assertEqual(data['items'][0]['location'], 'Room 13')
        self.assertEqual(data['unrecognized_day_count'], 1)
        self.assertEqual(data['schedule_type'], 'weekly_pattern')
        self.assertIn(WARNING, result['answer'])
        fixture.rag.retrieve_documents.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(CourseSchedule)), 8)
            saved = db.scalar(select(Message).where(Message.conversation_id == result['conversation_id'], Message.role == 'assistant'))
            self.assertEqual(saved.response_data['tool_results'][0]['total'], 4)

    def test_dates_and_relative_days_resolve_in_beijing(self):
        for args, date, day, ids in [({'relative_day': 'today'}, '2026-10-25', 'Sun', [16]),
                                   ({'relative_day': 'tomorrow'}, '2026-10-26', 'Mon', [13, 12, 11, 18]),
                                   ({'on_date': '2026-10-27'}, '2026-10-27', 'Tue', [14])]:
            self.timetable_plan(**args)
            data = self.ask().json()['tool_results'][0]
            self.assertEqual((data['matched_date'], data['matched_weekday']), (date, day))
            self.assertEqual([r['id'] for r in data['items']], ids)
            self.assertIsNone(data['filters']['relative_day'])
        # A UTC Sunday evening is already Monday in China.
        self.clock.now.side_effect = lambda tz: datetime(2026, 10, 25, 16, 30, tzinfo=timezone.utc).astimezone(tz)
        self.timetable_plan(relative_day='today')
        self.assertEqual(self.ask().json()['tool_results'][0]['matched_date'], '2026-10-26')

    def test_course_names_and_ids_do_not_guess_or_leak(self):
        self.timetable_plan(course_name='Database')
        data = self.ask().json()['tool_results'][0]
        self.assertEqual(data['reason'], 'ambiguous_course')
        self.assertEqual({r['id'] for r in data['courses']}, {1, 3})
        self.timetable_plan(course_name='database systems', weekday='Mon')
        self.assertEqual(self.ask().json()['tool_results'][0]['total'], 3)
        for args in ({'course_id': 2}, {'course_id': 999}, {'course_name': '%'}):
            self.timetable_plan(**args)
            data = self.ask().json()['tool_results'][0]
            self.assertEqual(data['reason'], 'course_not_found')
            self.assertEqual(data['courses'], [])

    def test_whole_week_pagination_unknown_day_and_empty_result(self):
        self.timetable_plan(limit=2, offset=2)
        data = self.ask().json()['tool_results'][0]
        self.assertEqual((data['total'], data['has_more']), (7, True))
        self.assertEqual([r['id'] for r in data['items']], [11, 18])
        self.timetable_plan(offset=6)
        row = self.ask().json()['tool_results'][0]['items'][0]
        self.assertEqual(row['id'], 17)
        self.assertIsNone(row['weekday'])
        self.timetable_plan(weekday='Wed')
        data = self.ask().json()['tool_results'][0]
        self.assertEqual((data['items'], data['total']), ([], 0))
        self.assertIn('不等于当天无课', data['warning'])

    def test_bad_arguments_document_priority_and_project_auth(self):
        for args in ({'user_id': 2}, {'course_id': True}, {'weekday': 'Monday'}, {'on_date': 'bad'},
                     {'relative_day': 'today', 'weekday': 'Mon'}, {'on_date': '2026-10-26', 'relative_day': 'tomorrow'},
                     {'course_id': 1, 'course_name': 'Database'}, {'limit': 21}, {'offset': -1}):
            self.timetable_plan(**args)
            self.assertEqual(self.ask().status_code, 502, args)
        self.llm.invoke.assert_not_called()
        self.timetable_plan(weekday='Mon')
        calls = self.planner.invoke.call_count
        self.assertEqual(self.ask(doc_ids=[1]).json()['answer'], 'document answer')
        self.assertEqual(self.client.post('/api/projects/2/ask', json={
            'query': '课表', 'use_school_tools': True}).status_code, 403)
        self.assertEqual(self.planner.invoke.call_count, calls)

    def test_durable_retry_keeps_original_resolved_day(self):
        self.timetable_plan(relative_day='tomorrow')
        durable = fixture.fixture.durable
        job = self.client.post('/api/projects/1/ask/jobs', json={
            'query': '明天的课表', 'use_school_tools': True}).json()
        with patch.object(durable.Checkpoints, 'finish', side_effect=RuntimeError('interrupted')):
            durable.run_workflow(job['job_id'])
        self.clock.now.return_value = datetime(2026, 10, 26, 12, tzinfo=fixture.school_qa.CHINA)
        self.assertEqual(self.client.post(f"/api/projects/1/jobs/{job['job_id']}/retry").status_code, 202)
        durable.run_workflow(job['job_id'])
        # 工具循环：route 选首个只读工具一次 + school_agent 续写一次（重复查询被去重阻断）；重试复用缓存不再调用。
        self.assertEqual(self.planner.invoke.call_count, 2)
        result = self.client.get(f"/api/projects/1/jobs/{job['job_id']}").json()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result']['tool_results'][0]['matched_date'], '2026-10-26')


if __name__ == '__main__':
    unittest.main()
