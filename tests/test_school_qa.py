"""Real authenticated QA graph and local queries; model tool calls are simulated."""
from datetime import datetime
import json
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import func, select

import test_backend_completion as fixture
from app.models.assignments import Assignment
from app.models.courses import Course
from app.models.message import Message
from app.services import school_qa

rag = fixture.fixture.rag


class SchoolQATests(unittest.TestCase):
    def setUp(self):
        fixture.BackendCompletionTests.setUp(self)
        self.client.app.include_router(rag.router, prefix='/api')
        self.llm = Mock()
        self.planner = Mock()
        self.llm.bind_tools.return_value = self.planner
        self.planner.bind.return_value = self.planner
        self.llm.bind.return_value = self.llm
        self.llm.invoke.return_value = AIMessage(content='查询到了你的作业。')
        self.clock = Mock(wraps=datetime)
        self.clock.now.return_value = datetime(2026, 10, 25, 12, tzinfo=school_qa.CHINA)
        self.patches.extend([
            patch.object(school_qa, 'model', return_value=self.llm),
            patch.object(school_qa, 'datetime', self.clock),
            patch.object(rag, 'get_history_messages', return_value=([], '', None)),
            patch.object(rag, 'rewrite_query', return_value='document query'),
            patch.object(rag, 'translate_query', return_value=None),
            patch.object(rag, 'retrieve_documents', return_value=([], [])),
            patch.object(rag, 'rerank_results', return_value=[]),
            patch.object(rag, 'generate_answer', return_value={'answer': 'document answer', 'sources': [], 'retrieved_contexts': []}),
        ])
        for item in self.patches[-8:]:
            item.start()
        with self.sessions() as db:
            db.get(Course, 1).course_name = 'Database Systems'
            db.get(Course, 1).last_synced_at = self.clock.now.return_value
            for ident, course, due, status in [(1, 1, 24, 'No submissions have been made yet'),
                                               (2, 1, 25, 'Submitted for grading'), (3, 2, 24, 'No submissions have been made yet'),
                                               (4, 1, 26, 'No submissions have been made yet')]:
                db.add(Assignment(id=ident, course_id=course, external_activity_id=str(ident), title=f'Lab {ident}',
                                  source_url=f'https://ispace.bnbu.edu.cn/mod/assign/view.php?id={ident}',
                                  due_at=datetime(2026, 10, due, 23, 59, tzinfo=school_qa.CHINA),
                                  submission_status=status, last_synced_at=self.clock.now.return_value))
            db.commit()
        self.plan(status='pending', relative_period='this_week')

    tearDown = fixture.BackendCompletionTests.tearDown

    def plan(self, **args):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[{'name': 'query_assignments', 'args': args, 'id': 'call1'}])

    def ask(self, **kwargs):
        return self.client.post('/api/projects/1/ask', json=dict(query='这周有哪些没交的作业？', use_school_tools=True, **kwargs))

    def test_tool_queries_owner_dates_and_saves_provenance(self):
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        data = result['tool_results'][0]
        self.assertEqual([item['id'] for item in data['items']], [1])
        self.assertEqual(data['filters']['due_from'], '2026-10-19')
        self.assertEqual(data['filters']['due_to'], '2026-10-25')
        self.assertEqual(data['synced_course_count'], 1)
        self.assertIn(school_qa.SNAPSHOT_WARNING, result['answer'])
        self.assertEqual(result['sources'], [])
        rag.retrieve_documents.assert_not_called()
        rag.rewrite_query.assert_not_called()
        messages = self.llm.invoke.call_args.args[0]
        self.assertIsInstance(messages[-1], ToolMessage)
        self.assertEqual(json.loads(messages[-1].content)['items'][0]['id'], 1)
        with self.sessions() as db:
            saved = db.scalar(select(Message).where(Message.conversation_id == result['conversation_id'], Message.role == 'assistant'))
            self.assertEqual(saved.response_data['tool_results'][0]['total'], 1)

    def test_explicit_document_scope_keeps_document_flow(self):
        for args in ({'doc_ids': [1]},
                     {'document_id': 1, 'selected_text': 'original'}):
            with self.subTest(args=args), patch.object(rag, 'search_document_first', return_value=[]):
                response = self.client.post('/api/projects/1/ask', json={'query': 'Explain the document', **args})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['answer'], 'document answer')
        self.llm.bind_tools.assert_not_called()

    def test_explicit_document_choice_uses_existing_rag(self):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[
            {'name': 'use_document_qa', 'args': {}, 'id': 'docs1'}])
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['answer'], 'document answer')
        rag.retrieve_documents.assert_called_once()

    def test_no_tool_choice_falls_back_to_document_retrieval(self):
        self.planner.invoke.return_value = AIMessage(content='Unstructured reply')
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['answer'], 'document answer')
        rag.retrieve_documents.assert_called_once()

    def test_auth_project_scope_and_tool_argument_isolation(self):
        self.assertEqual(self.client.post('/api/projects/2/ask', json={'query': '作业', 'use_school_tools': True}).status_code, 403)
        self.llm.bind_tools.assert_not_called()
        self.client.cookies.clear()
        self.assertEqual(self.ask().status_code, 401)
        self.llm.bind_tools.assert_not_called()
        self.client.cookies.set('session_id', 'token1')
        self.plan(user_id=2)
        self.assertEqual(self.ask().status_code, 502)
        self.llm.invoke.assert_not_called()
        self.plan(course_id=2)
        response = self.ask().json()['tool_results'][0]
        self.assertEqual(response['status'], 'clarification_required')
        self.assertEqual(response['courses'], [])

    def test_invalid_filters_unknown_tool_and_multiple_calls_do_not_save(self):
        for args in ({'due_from': '2026-10-26', 'due_to': '2026-10-25'}, {'due_to': '9999-12-31'},
                     {'limit': 10000}, {'relative_period': 'this_week', 'due_from': '2026-10-01'},
                     {'course_id': True}, {'status': 'done'}):
            self.plan(**args)
            self.assertEqual(self.ask().status_code, 502, args)
        for calls in ([{'name': 'submit_assignment', 'args': {}, 'id': 'a'}],
                      [{'name': 'query_assignments', 'args': {}, 'id': str(i)} for i in range(2)]):
            self.planner.invoke.return_value = AIMessage(content='', tool_calls=calls)
            self.assertEqual(self.ask().status_code, 502)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 2)  # Fixture history only.

    def test_course_name_resolution_and_ambiguity(self):
        self.plan(course_name='Database', status='pending', limit=1)
        data = self.ask().json()['tool_results'][0]
        self.assertEqual(data['total'], 2)
        self.assertTrue(data['has_more'])
        with self.sessions() as db:
            db.add(Course(id=3, user_id=1, course_name='Database Design'))
            db.commit()
        data = self.ask().json()['tool_results'][0]
        self.assertEqual(data['reason'], 'ambiguous_course')
        self.assertEqual({c['id'] for c in data['courses']}, {1, 3})
        self.plan(course_name='%')
        self.assertEqual(self.ask().json()['tool_results'][0]['reason'], 'course_not_found')

    def test_relative_periods_use_beijing_clock(self):
        for period, start, end in [('today', '2026-10-25', '2026-10-25'),
                                   ('tomorrow', '2026-10-26', '2026-10-26'),
                                   ('next_week', '2026-10-26', '2026-11-01')]:
            self.plan(relative_period=period)
            filters = self.ask().json()['tool_results'][0]['filters']
            self.assertEqual((filters['due_from'], filters['due_to']), (start, end))
        self.clock.now.assert_called_with(school_qa.CHINA)

    def test_truncated_or_empty_answer_never_saves(self):
        for response in (AIMessage(content='partial', response_metadata={'finish_reason': 'length'}), AIMessage(content='')):
            self.llm.invoke.return_value = response
            self.assertEqual(self.ask().status_code, 502)
        self.planner.invoke.side_effect = RuntimeError('upstream secret')
        response = self.ask()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('upstream secret', response.text)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 2)

    def test_durable_retry_reuses_tool_result_and_saves_messages_once(self):
        job = self.client.post('/api/projects/1/ask/jobs', json={'query': '本周未交作业', 'use_school_tools': True}).json()
        with patch.object(fixture.durable.Checkpoints, 'finish', side_effect=RuntimeError('commit interrupted')):
            fixture.durable.run_workflow(job['job_id'])
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 2)
        self.assertEqual(self.client.post(f"/api/projects/1/jobs/{job['job_id']}/retry").status_code, 202)
        fixture.durable.run_workflow(job['job_id'])
        fixture.durable.run_workflow(job['job_id'])
        # 工具循环：route 选首个工具一次 + school_agent 续写一次（重复查询被去重阻断），
        # 两处都在首轮执行、重试复用缓存不再调用，所以跨 3 次 run 规划器共 2 次、合成 1 次。
        self.assertEqual(self.planner.invoke.call_count, 2)
        self.llm.invoke.assert_called_once()
        result = self.client.get(f"/api/projects/1/jobs/{job['job_id']}").json()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['result']['tool_results'][0]['items'][0]['id'], 1)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 4)


if __name__ == '__main__':
    unittest.main()
