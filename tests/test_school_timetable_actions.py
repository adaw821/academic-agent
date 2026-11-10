"""Exercise agent routing through authenticated QA, with school/model I/O mocked."""
import unittest
from unittest.mock import patch, AsyncMock
from langchain_core.messages import AIMessage
from sqlalchemy import select
import test_school_qa as fixture
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.services import timetable_actions as actions


class TimetableActionTests(unittest.TestCase):
    setUp = fixture.SchoolQATests.setUp
    tearDown = fixture.SchoolQATests.tearDown
    plan = fixture.SchoolQATests.plan

    def action(self, name, args=None, **extra):
        """经规划器路由：读工具直接执行，写工具只返回确认卡片。"""
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[
            dict(name=name, args=args or {}, id='timetable-action')])
        return self.client.post('/api/projects/1/ask', json=dict(
            query='请执行这个课表操作', **extra))

    def confirm(self, name, args=None, **extra):
        """模拟用户点「确认执行」：回传写操作，直接落地。"""
        return self.client.post('/api/projects/1/ask', json=dict(
            query='确认执行', confirm_action=dict(name=name, args=args or {}), **extra))

    def test_add_edit_calendar_delete_and_owner_isolation(self):
        fields = dict(day_of_week='Mon', start_time='09:00', end_time='10:00', location='A101')
        result = self.confirm('add_timetable_entry', dict(course_name='Database Systems', **fields))
        self.assertEqual(result.status_code, 200, result.text)
        ident = result.json()['tool_results'][0]['id']
        result = self.confirm('update_timetable_entry', dict(schedule_id=ident, **{**fields, 'location': 'B202'}))
        self.assertEqual(result.status_code, 200, result.text)
        result = self.confirm('set_timetable_calendar', dict(schedule_id=ident, term_start='2026-09-01', term_end='2026-12-31', week_pattern='odd'))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.confirm('update_course_details', dict(course_id=1, teachers='新老师')).status_code, 200)
        with self.sessions() as db:
            self.assertEqual(db.get(Course, 1).course_name, 'Database Systems')
            self.assertEqual(db.get(Course, 1).teachers, '新老师')
            self.assertEqual(db.get(CourseSchedule, ident).location, 'B202')
            self.assertEqual(db.get(CourseSchedule, ident).week_pattern, 'odd')
        self.assertEqual(self.confirm('update_course_details', dict(course_id=2, teachers='不可改')).status_code, 404)
        with self.sessions() as db:
            db.add(CourseSchedule(id=999, course_id=2, **fields))
            db.commit()
        self.assertEqual(self.confirm('delete_timetable_entry', dict(schedule_id=999)).status_code, 404)
        self.assertEqual(self.confirm('delete_timetable_entry', dict(schedule_id=ident)).status_code, 200)
        with self.sessions() as db:
            self.assertIsNone(db.get(CourseSchedule, ident))
            self.assertIsNotNone(db.get(Course, 1))

    def test_write_actions_need_confirmation_and_reject_tampered_args(self):
        # 规划器点到写操作时，先返回确认卡片（pending_action），不直接执行。
        card = self.action('delete_timetable_entry', {'schedule_id': 1})
        self.assertEqual(card.status_code, 200, card.text)
        self.assertEqual(card.json()['pending_action']['name'], 'delete_timetable_entry')
        # 确认回传的参数被篡改/非法时拒绝执行。
        for name, args in [('delete_timetable_entry', {'schedule_id': True}),
                           ('update_course_details', {'course_id': True, 'teachers': 'x'})]:
            self.assertEqual(self.confirm(name, args).status_code, 400)
        # 只读工具不走确认通道，非法身份参数仍被规划器拒绝。
        self.assertEqual(self.action('get_mis_timetable', {'user_id': 2}).status_code, 502)

    def test_browser_is_blocked_for_nonlocal_requests_and_status_is_truthful(self):
        with patch('app.services.mis_timetable.enabled', return_value=True), patch('app.services.mis_timetable.mis_capture.start') as start:
            # TestClient's peer is testclient, not localhost.
            self.assertEqual(self.action('open_mis_timetable').status_code, 403)
            start.assert_not_called()
        with patch('app.services.mis_timetable.read_snapshot', return_value=None), patch('app.services.mis_timetable.mis_capture.status', return_value={'state': 'saving', 'message': '正在保存'}):
            response = self.action('get_mis_timetable')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('已保存学期', response.json()['answer'])
        with self.sessions() as db, patch('app.services.mis_timetable.enabled', return_value=True), patch('app.services.mis_timetable.mis_capture.request_capture', return_value={'state': 'saving', 'message': '正在保存'}) as save:
            db.info['local_browser_allowed'] = True
            data = actions.execute(db, 1, 'save_mis_timetable', actions.EmptyArgs())
            save.assert_called_once_with(1)
            self.assertIn('尚未确认完成', actions.answer(dict(data, tool='save_mis_timetable'))['answer'])

    def test_bound_attachment_and_missing_mis_image(self):
        with patch('app.services.timetable_attachments.read_image', return_value=b'image') as read, patch('app.services.timetable_service.timetable_service', new_callable=AsyncMock, return_value={'timetables': []}) as recognize:
            result = self.action('supplement_timetable_image', {'source': 'attachment'}, timetable_image_id='a' * 32)
            self.assertEqual(result.status_code, 200, result.text)
            read.assert_called_with(1, 'a' * 32)
            self.assertEqual(recognize.call_args.args[3], 1)
        with patch('app.services.mis_timetable.read_snapshot', return_value=None):
            self.assertEqual(self.action('supplement_timetable_image', {'source': 'mis'}).status_code, 404)

    def test_durable_retry_does_not_repeat_completed_mutation(self):
        job = self.client.post('/api/projects/1/ask/jobs', json=dict(
            query='确认执行', confirm_action=dict(name='add_timetable_entry',
                args=dict(course_name='New Course', day_of_week='Fri', start_time='10:00', end_time='11:00')))).json()['job_id']
        durable = fixture.fixture.durable
        with patch.object(fixture.rag, 'history_messages_control', side_effect=RuntimeError('save interrupted')):
            durable.run_workflow(job)
        self.assertEqual(self.client.post(f'/api/projects/1/jobs/{job}/retry').status_code, 202)
        durable.run_workflow(job)
        self.assertEqual(self.client.get(f'/api/projects/1/jobs/{job}').json()['status'], 'completed')
        with self.sessions() as db:
            self.assertEqual(len(list(db.scalars(select(CourseSchedule).join(Course).where(Course.course_name == 'New Course')))), 1)


if __name__ == '__main__':
    unittest.main()
