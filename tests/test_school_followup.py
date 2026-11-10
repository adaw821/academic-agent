"""Write-result preservation and strictly read-only continuation, with mocked inference."""
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage

import test_school_sync_qa as sync_fixture
from app.models.courses import Course
from app.services import school_qa as school


def call(name='query_assignments', **args):
    return AIMessage(content='', tool_calls=[dict(name=name, args=args, id='next')])


class SchoolFollowupTests(unittest.TestCase):
    setUp = sync_fixture.SchoolSyncQATests.setUp
    tearDown = sync_fixture.SchoolSyncQATests.tearDown
    plan = sync_fixture.SchoolSyncQATests.plan
    sync_plan = sync_fixture.SchoolSyncQATests.sync_plan
    ask = sync_fixture.SchoolSyncQATests.ask

    def test_sync_reads_fresh_data_and_keeps_fixed_prefix_and_history(self):
        history = [{'role': 'user', 'content': '数据库课同步后列出作业和截止时间'}]
        self.planner.invoke.side_effect = [self.planner.invoke.return_value,
                                         call(course_id=1), AIMessage(content='结束')]
        with patch.object(sync_fixture.rag, 'get_history_messages', return_value=(history, '课程摘要', None)):
            response = self.ask(query='同步吧')
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        fixed = school.answer_course_sync(result['tool_results'][0])['answer']
        self.assertEqual(result['answer'], fixed + '\n\n补充查询：\n查询到了你的作业。')
        self.assertIn('Refreshed lab', json.dumps(result['tool_results'][1]))
        self.collect.assert_called_once()
        messages = self.planner.invoke.call_args_list[1].args[0]
        self.assertIn(history[0]['content'], messages[1].content)
        self.assertIn('课程摘要', messages[1].content)
        self.assertIn('executed_operation', messages[2].content)
        schemas = self.llm.bind_tools.call_args_list[-1].args[0]
        self.assertEqual({s['function']['name'] for s in schemas}, school.LOOP_READ_TOOLS)
        synthesis = self.llm.invoke.call_args.args[0]
        self.assertIn(history[0]['content'], synthesis[1].content)

    def test_confirmed_write_then_read_uses_committed_data(self):
        self.planner.invoke.side_effect = [call('query_courses', course_id=1), AIMessage(content='结束')]
        response = self.client.post('/api/projects/1/ask', json=dict(
            query='确认执行', confirm_action=dict(name='update_course_details',
                args=dict(course_id=1, course_name='Renamed course'))))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result['answer'].startswith(school.answer_action(result['tool_results'][0])['answer']))
        self.assertIn('Renamed course', json.dumps(result['tool_results'][1]))
        with self.sessions() as db:
            self.assertEqual(db.get(Course, 1).course_name, 'Renamed course')

    def test_no_tool_discards_planner_text(self):
        self.planner.invoke.side_effect = [self.planner.invoke.return_value, AIMessage(content='已同步999项')]
        result = self.ask().json()
        self.assertEqual(result, dict(result, **school.answer_course_sync(result['tool_results'][0])))
        self.llm.invoke.assert_not_called()

    def test_model_failure_after_sync_preserves_success(self):
        self.planner.invoke.side_effect = [self.planner.invoke.return_value, RuntimeError('private detail')]
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('private detail', response.text)
        self.assertEqual(response.json()['tool_results'][0]['status'], 'completed')
        self.collect.assert_called_once()

    def test_clarification_does_not_continue(self):
        self.sync_plan()
        result = self.ask().json()
        self.assertEqual(result['tool_results'][0]['status'], 'clarification_required')
        self.planner.invoke.assert_called_once()

    def base(self):
        return dict(answer='固定写结果', sources=[], retrieved_contexts=[],
                    tool_results=[dict(tool='sync_school_course', status='completed', course_id=1)])

    def follow(self, base=None):
        with self.sessions() as db:
            return school.follow_up_school_write(db, 1, '确认执行', [], '', base or self.base(), project_id=1)

    def test_all_nonread_tools_blocked_before_dispatch(self):
        for name in [*school.WRITE_TOOLS, *school.DIRECT_ACTIONS, 'sync_school_course',
                     'use_document_qa', 'clarify_school_request', 'unknown_tool']:
            with self.subTest(name=name), patch.object(school, 'query_assignments') as dispatch:
                self.planner.invoke.return_value = call(name)
                self.assertEqual(self.follow(), self.base())
                dispatch.assert_not_called()

    def test_invalid_args_multiple_calls_and_invalid_calls_do_not_execute(self):
        bad = call(course_id=True)
        multiple = AIMessage(content='', tool_calls=call(course_id=1).tool_calls * 2)
        invalid = AIMessage(content='', invalid_tool_calls=[dict(name='query_assignments', args='{', id='x', error='bad')])
        for reply in [bad, multiple, invalid]:
            with patch.object(school, 'query_assignments') as dispatch:
                self.planner.invoke.return_value = reply
                self.assertEqual(self.follow(), self.base())
                dispatch.assert_not_called()

    def test_duplicate_read_is_not_repeated(self):
        self.planner.invoke.return_value = call(course_id=1)
        with patch.object(school, 'query_assignments', wraps=school.query_assignments) as dispatch:
            result = self.follow()
            dispatch.assert_called_once()
        self.assertEqual(len(result['tool_results']), 2)

    def test_read_loop_has_hard_step_limit(self):
        self.planner.invoke.side_effect = [call(course_id=1, offset=i) for i in range(10)]
        result = self.follow()
        self.assertEqual(self.planner.invoke.call_count, school.MAX_FOLLOWUP_STEPS)
        self.assertEqual(len(result['tool_results']), 1 + school.MAX_FOLLOWUP_STEPS)

    def test_cross_user_read_stops_with_clarification_without_leaking_data(self):
        self.planner.invoke.return_value = call(course_id=2)
        result = self.follow()
        self.planner.invoke.assert_called_once()
        self.assertEqual(result['tool_results'][1]['status'], 'clarification_required')
        self.assertEqual(result['tool_results'][1]['courses'], [])

    def test_read_error_rolls_back_only_followup_and_preserves_write(self):
        self.planner.invoke.side_effect = [self.planner.invoke.return_value, call(course_id=1)]
        original_query = school.query_assignments

        def fail_read(db, user_id, plan, **kwargs):
            if plan['name'] in school.LOOP_READ_TOOLS:
                raise RuntimeError('private read failure')
            return original_query(db, user_id, plan, **kwargs)

        with patch.object(school, 'query_assignments', side_effect=fail_read):
            response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['tool_results']), 1)
        with self.sessions() as db:
            from app.models.assignments import Assignment
            self.assertEqual(db.get(Assignment, 1).title, 'Refreshed lab')

    def test_synthesis_failure_returns_fixed_answer(self):
        self.planner.invoke.return_value = call(course_id=1)
        self.llm.invoke.side_effect = RuntimeError('private model failure')
        self.assertEqual(self.follow(), self.base())

    def test_pending_or_unsuccessful_operations_do_not_continue(self):
        for name, status in [('sync_school_course', 'clarification_required'),
                             ('update_course_details', 'confirm_required'),
                             ('import_school_resource', 'queued'),
                             ('import_school_resource', 'failed')]:
            base = self.base()
            base['tool_results'][0].update(tool=name, status=status)
            self.assertEqual(self.follow(base), base)
        self.planner.invoke.assert_not_called()

    def test_durable_resume_after_followup_interruption_never_repeats_sync(self):
        job = self.client.post('/api/projects/1/ask/jobs', json=dict(query='同步数据库课')).json()['job_id']
        durable = sync_fixture.fixture.fixture.durable
        with patch.object(school, 'follow_up_school_write', side_effect=RuntimeError('interrupted')):
            durable.run_workflow(job)
        self.assertEqual(self.client.get(f'/api/projects/1/jobs/{job}').json()['status'], 'failed')
        self.assertEqual(self.client.post(f'/api/projects/1/jobs/{job}/retry').status_code, 202)
        self.planner.invoke.side_effect = [call(course_id=1), AIMessage(content='结束')]
        durable.run_workflow(job)
        result = self.client.get(f'/api/projects/1/jobs/{job}').json()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(result['result']['tool_results']), 2)
        self.collect.assert_called_once()


if __name__ == '__main__':
    unittest.main()
