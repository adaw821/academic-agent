"""Authenticated chat sync with real persistence; school and planner are simulated."""
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage
from sqlalchemy import func, select, text

import test_school_qa as fixture
from app.models.assignments import Assignment
from app.models.courses import Course
from app.models.message import Message
from app.services.ispace import CourseSnapshot, SchoolLoginRequired, SchoolError

rag = fixture.rag
URL = 'https://ispace.bnbu.edu.cn/course/view.php?id=11405'
NEW_URL = 'https://ispace.bnbu.edu.cn/course/view.php?id=22222'


class SchoolSyncQATests(unittest.TestCase):
    plan = fixture.SchoolQATests.plan
    tearDown = fixture.SchoolQATests.tearDown

    def setUp(self):
        fixture.SchoolQATests.setUp(self)
        with self.sessions() as db:
            db.get(Course, 1).source_url = URL
            if db.get_bind().dialect.name == 'postgresql':
                # The shared fixture seeds explicit IDs; PostgreSQL does not advance
                # serial sequences for those inserts. Only repair this test schema.
                assert self.schema and self.schema.startswith('backend_test_')
                for model in (Course, Assignment):
                    db.execute(text("SELECT setval(pg_get_serial_sequence(:table_name, 'id'), :highest, true)"),
                               dict(table_name=f'{self.schema}.{model.__tablename__}',
                                    highest=db.scalar(select(func.max(model.id)))))
            db.commit()
        self.patches.extend([
            patch('app.routers.course_sync.load_school_session', return_value='unused-test-session'),
            patch('app.routers.course_sync.ISpaceClient'),
        ])
        self.credentials = self.patches[-2].start()
        client = self.patches[-1].start()
        self.collect = client.return_value.__enter__.return_value.collect_course
        self.collect.return_value = CourseSnapshot('11405', 'Database Systems', URL, assignments=[
            dict(external_activity_id='1', title='Refreshed lab',
                 source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id=1',
                 submission_status='Submitted for grading'),
        ], warnings=['Partial snapshot'])
        self.patches.extend([
            patch('app.routers.school.load_school_session', return_value='unused-school-session'),
            patch('app.routers.school.ISpaceClient'),
        ])
        self.discovery_credentials = self.patches[-2].start()
        school = self.patches[-1].start().return_value.__enter__.return_value
        self.discover, self.import_collect = school.discover_courses, school.collect_course
        self.discover.return_value = dict(courses=[dict(external_course_id='22222', title='Software Engineering',
                                                       source_url=NEW_URL)], complete=True, warning=None)
        self.import_collect.return_value = CourseSnapshot('22222', 'Software Engineering', NEW_URL,
            assignments=[dict(external_activity_id='222', title='First lab',
                source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id=222')])
        self.sync_plan(course_id=1)

    def sync_plan(self, **args):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[
            {'name': 'sync_school_course', 'args': args, 'id': 'sync1'}])

    def ask(self, **kwargs):
        payload = dict(query='请同步 Database Systems 课程', use_school_tools=True, allow_school_sync=True)
        payload.update(kwargs)
        return self.client.post('/api/projects/1/ask', json=payload)

    def test_sync_persists_owned_course_and_reports_actual_result(self):
        self.sync_plan(course_name='database systems')
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        data = result['tool_results'][0]
        self.assertEqual((data['status'], data['course_id'], data['assignments']), ('completed', 1, 1))
        self.assertIn('Partial snapshot', result['answer'])
        self.assertIn('未下载附件', result['answer'])
        self.credentials.assert_called_once_with(1)
        self.collect.assert_called_once_with(URL)
        self.discover.assert_not_called()
        self.llm.invoke.assert_not_called()
        rag.retrieve_documents.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.get(Assignment, 1).title, 'Refreshed lab')
            self.assertEqual(db.get(Assignment, 3).title, 'Lab 3')
            saved = db.scalar(select(Message).where(Message.conversation_id == result['conversation_id'], Message.role == 'assistant'))
            self.assertEqual(saved.response_data['tool_results'][0]['status'], 'completed')

    def test_sync_tool_always_exposed_and_executes_without_a_flag(self):
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        schemas = self.llm.bind_tools.call_args_list[0].args[0]  # Initial planner; follow-up is read-only.
        self.assertIn('sync_school_course', [s['function']['name'] for s in schemas])
        self.collect.assert_called_once_with(URL)

    def test_document_scope_never_syncs(self):
        for args in ({'doc_ids': [1]}, {'document_id': 1, 'selected_text': 'sync this'}):
            with patch.object(rag, 'search_document_first', return_value=[]):
                self.assertEqual(self.ask(**args).json()['answer'], 'document answer')
        self.llm.bind_tools.assert_not_called()
        self.collect.assert_not_called()

    def test_ownership_and_missing_target_before_credentials(self):
        for args, reason in [({}, 'course_required'), ({'course_id': 2}, 'course_not_found'),
                             ({'course_id': 999}, 'course_not_found')]:
            self.sync_plan(**args)
            result = self.ask().json()
            self.assertEqual(result['tool_results'][0]['reason'], reason)
            self.assertEqual(result['tool_results'][0]['courses'], [])
            self.assertIn('尚未执行同步', result['answer'])
        self.credentials.assert_not_called()
        self.collect.assert_not_called()
        self.discovery_credentials.assert_not_called()

    def test_ambiguous_names_and_exact_match(self):
        with self.sessions() as db:
            db.add(Course(id=3, user_id=1, course_name='Database Design'))
            db.commit()
        self.sync_plan(course_name='Database')
        data = self.ask().json()['tool_results'][0]
        self.assertEqual(data['reason'], 'ambiguous_course')
        self.assertEqual({c['id'] for c in data['courses']}, {1, 3})
        self.collect.assert_not_called()
        self.sync_plan(course_name='Database Systems')
        self.assertEqual(self.ask().json()['tool_results'][0]['course_id'], 1)

    def test_invalid_model_arguments_cannot_choose_identity_or_url(self):
        for args in ({'course_id': True}, {'course_id': 1, 'user_id': 2},
                     {'course_id': 1, 'course_name': 'Database'}, {'source_url': URL}, {'course_ids': [1, 2]}):
            self.sync_plan(**args)
            self.assertEqual(self.ask().status_code, 502)
        self.collect.assert_not_called()

    def test_auth_and_project_access_before_planning(self):
        self.client.cookies.clear()
        self.assertEqual(self.ask().status_code, 401)
        self.client.cookies.set('session_id', 'token2')
        self.assertEqual(self.ask().status_code, 403)
        self.llm.bind_tools.assert_not_called()
        self.collect.assert_not_called()

    def test_expired_session_and_upstream_failure_keep_previous_data(self):
        for error, status in [(SchoolLoginRequired('secret'), 409), (SchoolError('secret'), 502)]:
            self.collect.side_effect = error
            response = self.ask()
            self.assertEqual(response.status_code, status)
            self.assertNotIn('secret', response.text)
        with self.sessions() as db:
            self.assertEqual(db.get(Assignment, 1).title, 'Lab 1')
            self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 2)

    def test_unlinked_course_cannot_sync(self):
        with self.sessions() as db:
            db.get(Course, 1).source_url = None
            db.commit()
        self.assertEqual(self.ask().status_code, 409)
        self.credentials.assert_not_called()

    def test_first_import_by_name_is_persisted_and_retries_reuse_same_course(self):
        for name in ['software engineering', 'Engineering']:
            # Force the discovery path again with a selected school ID on the second call.
            self.sync_plan(**({'course_name': name} if name.startswith('software') else {'external_course_id': '22222'}))
            response = self.ask()
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()['tool_results'][0]
            self.assertEqual(data['status'], 'completed')
            self.assertEqual(data['lookup_source'], 'school')
            self.assertEqual(data['assignments'], 1)
        with self.sessions() as db:
            courses = db.scalars(select(Course).where(Course.source_url == NEW_URL, Course.user_id == 1)).all()
            self.assertEqual(len(courses), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(Assignment).where(Assignment.course_id == courses[0].id)), 1)
        self.collect.assert_not_called()
        self.llm.invoke.assert_not_called()
        self.assertTrue(all(call.args == (1,) for call in self.discovery_credentials.call_args_list))

    def test_school_name_matching_exact_then_literal_substring(self):
        self.discover.return_value['courses'].append(dict(external_course_id='22223', title='Software Engineering Lab',
            source_url=NEW_URL.replace('22222', '22223')))
        self.sync_plan(course_name='engineering')
        result = self.ask().json()['tool_results'][0]
        self.assertEqual(result['reason'], 'ambiguous_school_course')
        self.import_collect.assert_not_called()
        self.sync_plan(course_name='Software Engineering')
        self.assertEqual(self.ask().json()['tool_results'][0]['status'], 'completed')
        self.import_collect.assert_called_once_with(NEW_URL)

    def test_school_ambiguity_selection_rediscovers_and_distinguishes_ids(self):
        self.discover.return_value['courses'].append(dict(external_course_id='22223', title='Software Engineering',
            source_url=NEW_URL.replace('22222', '22223')))
        self.sync_plan(course_name='Software Engineering')
        result = self.ask().json()
        self.assertEqual(result['tool_results'][0]['reason'], 'ambiguous_school_course')
        self.assertIn('学校课程编号：22223', result['answer'])
        self.import_collect.assert_not_called()
        self.sync_plan(external_course_id='22223')
        self.import_collect.return_value = CourseSnapshot('22223', 'Software Engineering', NEW_URL.replace('22222', '22223'))
        self.assertEqual(self.ask(query='同步学校课程编号 22223').status_code, 200)
        self.assertEqual(self.discover.call_count, 2)
        self.import_collect.assert_called_once_with(NEW_URL.replace('22222', '22223'))

    def test_partial_discovery_needs_explicit_rediscovered_id(self):
        self.discover.return_value['complete'] = False
        self.sync_plan(course_name='Software Engineering')
        data = self.ask().json()['tool_results'][0]
        self.assertEqual(data['reason'], 'school_discovery_incomplete')
        self.import_collect.assert_not_called()
        self.sync_plan(external_course_id='99999')
        self.assertEqual(self.ask().json()['tool_results'][0]['status'], 'clarification_required')
        self.import_collect.assert_not_called()
        self.sync_plan(external_course_id='22222')
        result = self.ask().json()['tool_results'][0]
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(result['discovery_complete'])
        self.assertTrue(result['warnings'])

    def test_missing_school_name_does_not_guess_or_treat_sql_wildcards_as_patterns(self):
        for name in ['%', '_', '不存在的课程']:
            self.sync_plan(course_name=name)
            data = self.ask().json()['tool_results'][0]
            self.assertEqual(data['reason'], 'school_course_not_found')
            self.assertEqual(data['courses'][0]['external_course_id'], '22222')
        self.import_collect.assert_not_called()

    def test_new_import_cannot_reuse_another_users_local_course(self):
        with self.sessions() as db:
            db.add(Course(id=9, user_id=2, source_url=NEW_URL, course_name='Other owner'))
            db.commit()
        self.sync_plan(course_name='Software')
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(response.json()['tool_results'][0]['course_id'], 9)
        with self.sessions() as db:
            self.assertEqual(db.get(Course, 9).course_name, 'Other owner')

    def test_discovery_and_collection_errors_never_report_success_or_leak_details(self):
        self.sync_plan(course_name='Software')
        for method in (self.discover, self.import_collect):
            for error, code in [(SchoolLoginRequired('secret-cookie'), 409), (SchoolError('secret-cookie'), 502)]:
                method.side_effect = error
                response = self.ask()
                self.assertEqual(response.status_code, code, response.text)
                self.assertNotIn('secret-cookie', response.text)
                method.side_effect = None
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Course).where(Course.source_url == NEW_URL)), 0)

    def test_invalid_discovered_url_and_changed_snapshot_source_are_rejected(self):
        self.sync_plan(course_name='Software')
        self.discover.return_value['courses'][0]['source_url'] = 'https://example.com/course/view.php?id=22222'
        self.assertEqual(self.ask().status_code, 502)
        self.import_collect.assert_not_called()
        self.discover.return_value['courses'][0]['source_url'] = NEW_URL
        self.import_collect.return_value.source_url = URL
        self.assertEqual(self.ask().status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Course).where(Course.source_url == NEW_URL)), 0)

    def test_school_id_validation_rejects_bad_identifiers(self):
        for args in [{'external_course_id': 22222.0}, {'external_course_id': True},
                     {'external_course_id': '0'}, {'external_course_id': '22222', 'course_id': 1},
                     {'external_course_id': -1}, {'external_course_id': '22222.0'}]:
            self.sync_plan(**args)
            self.assertEqual(self.ask().status_code, 502)
        self.discover.assert_not_called()

    def match_reply(self, indices, confident=True):
        return AIMessage(content='', tool_calls=[{'name': 'resolve_course_candidates',
            'args': dict(indices=indices, confident=confident), 'id': 'match1'}])

    def test_numeric_school_id_with_display_name_is_accepted(self):
        self.sync_plan(external_course_id=22222, course_name='Software Engineering')
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['tool_results'][0]['filters']['external_course_id'], '22222')
        self.import_collect.assert_called_once_with(NEW_URL)

    def test_pasted_school_candidate_overrides_misrouted_local_id(self):
        self.sync_plan(course_id=22222, course_name='Software Engineering')
        response = self.ask(query='Software Engineering（学校课程编号：22222）这个')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['tool_results'][0]['status'], 'completed')
        self.import_collect.assert_called_once_with(NEW_URL)
        self.collect.assert_not_called()

    def test_explicit_school_id_does_not_remove_unknown_parameters(self):
        self.sync_plan(course_id=22222, user_id=2)
        self.assertEqual(self.ask(query='同步学校课程编号 22222').status_code, 502)
        self.discover.assert_not_called()

    def test_dbms_resolves_against_owned_local_names(self):
        self.sync_plan(course_name='dbms')
        self.planner.invoke.side_effect = [self.planner.invoke.return_value, self.match_reply([0])]
        response = self.ask(query='帮我同步 dbms')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['tool_results'][0]['course_id'], 1)
        self.collect.assert_called_once_with(URL)
        self.discover.assert_not_called()
        matching_prompt = self.planner.invoke.call_args.args[0][-1].content
        self.assertIn('Database Systems', matching_prompt)
        self.assertNotIn('Other', matching_prompt)
        self.assertNotIn('source_url', matching_prompt)

    def test_chinese_alias_resolves_school_candidate_after_no_local_match(self):
        self.sync_plan(course_name='软工')
        self.planner.invoke.side_effect = [self.planner.invoke.return_value,
            self.match_reply([], False), self.match_reply([0])]
        response = self.ask(query='帮我同步软工')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['tool_results'][0]['lookup_source'], 'school')
        self.import_collect.assert_called_once_with(NEW_URL)

    def test_semantic_ambiguity_and_low_confidence_never_sync(self):
        with self.sessions() as db:
            db.add(Course(id=3, user_id=1, course_name='Database Management Systems'))
            db.commit()
        for indices, confident in [([0, 1], True), ([0], False)]:
            self.sync_plan(course_name='数据库')
            self.planner.invoke.side_effect = [self.planner.invoke.return_value, self.match_reply(indices, confident)]
            response = self.ask(query='同步数据库')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['tool_results'][0]['status'], 'clarification_required')
        self.collect.assert_not_called()
        self.import_collect.assert_not_called()

    def test_semantic_match_in_partial_school_list_needs_confirmation(self):
        self.discover.return_value['complete'] = False
        self.sync_plan(course_name='SE')
        self.planner.invoke.side_effect = [self.planner.invoke.return_value,
            self.match_reply([], False), self.match_reply([0])]
        data = self.ask(query='同步 SE').json()['tool_results'][0]
        self.assertEqual(data['reason'], 'school_discovery_incomplete')
        self.import_collect.assert_not_called()

    def test_semantic_duplicate_titles_cannot_be_hidden_by_model(self):
        self.discover.return_value['courses'].append(dict(external_course_id='22223', title='Software Engineering',
            source_url=NEW_URL.replace('22222', '22223')))
        self.sync_plan(course_name='SE')
        self.planner.invoke.side_effect = [self.planner.invoke.return_value,
            self.match_reply([], False), self.match_reply([0])]
        data = self.ask(query='同步 SE').json()['tool_results'][0]
        self.assertEqual(data['reason'], 'ambiguous_school_course')
        self.assertEqual(len(data['courses']), 2)
        self.import_collect.assert_not_called()

    def test_semantic_invalid_indices_and_model_failure_cannot_write(self):
        for reply in [self.match_reply([999]), self.match_reply([-1]), self.match_reply([True]),
                      RuntimeError('secret-key')]:
            self.sync_plan(course_name='不存在的别名')
            self.planner.invoke.side_effect = [self.planner.invoke.return_value, reply, reply]
            response = self.ask()
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['tool_results'][0]['status'], 'clarification_required')
            self.assertNotIn('secret-key', response.text)
        self.collect.assert_not_called()
        self.import_collect.assert_not_called()

    def test_semantic_candidate_limit_does_not_assume_unique_from_partial_list(self):
        from app.services.course_matching import semantic_course_matches
        candidates = [dict(name=f'Course {i}') for i in range(201)]
        matches, confirmation = semantic_course_matches('课程缩写', candidates)
        self.assertTrue(confirmation)
        self.assertEqual(len(matches), 10)
        self.llm.bind_tools.assert_not_called()

    def test_first_import_durable_recovery_reuses_committed_result(self):
        self.sync_plan(course_name='Software')
        job = self.client.post('/api/projects/1/ask/jobs', json=dict(
            query='同步 Software 课程', use_school_tools=True, allow_school_sync=True)).json()['job_id']
        durable = fixture.fixture.durable
        with patch.object(rag, 'history_messages_control', side_effect=RuntimeError('save interrupted')):
            durable.run_workflow(job)
        self.assertEqual(self.client.get(f'/api/projects/1/jobs/{job}').json()['status'], 'failed')
        self.assertEqual(self.client.post(f'/api/projects/1/jobs/{job}/retry').status_code, 202)
        durable.run_workflow(job)
        self.assertEqual(self.client.get(f'/api/projects/1/jobs/{job}').json()['status'], 'completed')
        self.discover.assert_called_once()
        self.import_collect.assert_called_once()

    def test_durable_retry_after_save_failure_reuses_committed_sync(self):
        job = self.client.post('/api/projects/1/ask/jobs', json=dict(
            query='同步课程 1', use_school_tools=True, allow_school_sync=True)).json()['job_id']
        durable = fixture.fixture.durable
        with patch.object(rag, 'history_messages_control', side_effect=RuntimeError('save interrupted')):
            durable.run_workflow(job)
        self.assertEqual(self.client.get(f'/api/projects/1/jobs/{job}').json()['status'], 'failed')
        self.assertEqual(self.client.post(f'/api/projects/1/jobs/{job}/retry').status_code, 202)
        durable.run_workflow(job)
        durable.run_workflow(job)
        result = self.client.get(f'/api/projects/1/jobs/{job}').json()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['result']['tool_results'][0]['status'], 'completed')
        self.collect.assert_called_once()
        self.assertEqual(self.planner.invoke.call_count, 2)  # Initial plan plus read-only follow-up decision.
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 4)


if __name__ == '__main__':
    unittest.main()
