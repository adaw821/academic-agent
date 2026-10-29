"""Course inventory tool: authenticated routing, coverage, filters and no writes."""
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage
from sqlalchemy import select

import test_school_qa as fixture
from app.models.courses import Course
from app.models.course_resources import CourseResource
from app.models.message import Message

rag = fixture.rag


class SchoolCourseQATests(unittest.TestCase):
    plan = fixture.SchoolQATests.plan
    tearDown = fixture.SchoolQATests.tearDown

    def setUp(self):
        fixture.SchoolQATests.setUp(self)
        with self.sessions() as db:
            db.get(Course, 1).external_course_id = '11405'
            for ident, name, synced in [(3, 'Empty Course', True), (4, 'Submission Only', True),
                                        (5, 'Resource Only', False), (6, 'Timetable Only', False)]:
                db.add(Course(id=ident, user_id=1, course_name=name,
                              last_synced_at=self.clock.now.return_value if synced else None))
            db.flush()
            for ident, course, purpose in [(100, 1, 'lecture'), (101, 1, 'lecture'),
                                           (102, 4, 'student_submission'), (103, 5, 'lecture')]:
                db.add(CourseResource(id=ident, course_id=course, title='Test resource', source_key=str(ident),
                    resource_type='resource', purpose=purpose,
                    source_url=f'https://ispace.bnbu.edu.cn/mod/resource/view.php?id={ident}',
                    last_synced_at=self.clock.now.return_value))
            db.commit()
        self.course_plan()

    def course_plan(self, **args):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[
            {'name': 'query_courses', 'args': args, 'id': 'courses1'}])

    def ask(self, **kwargs):
        payload = dict(query='有没有匹配课程是没有内容的', use_school_tools=True)
        payload.update(kwargs)
        return self.client.post('/api/projects/1/ask', json=payload)

    def test_course_question_routes_to_inventory_without_document_retrieval(self):
        self.course_plan(content_state='no_synced_content')
        with patch('app.routers.school.load_school_session') as credentials:
            response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()['tool_results'][0]
        self.assertEqual(data['tool'], 'query_courses')
        self.assertEqual({r['id'] for r in data['items']}, {3, 4, 6})
        self.assertEqual(data['matched_course_count'], 5)
        self.assertIn('不证明学校课程为空', response.json()['answer'])
        rag.retrieve_documents.assert_not_called()
        credentials.assert_not_called()
        with self.sessions() as db:
            saved = db.scalar(select(Message).where(Message.conversation_id == response.json()['conversation_id'],
                                                    Message.role == 'assistant'))
            self.assertEqual(saved.response_data['tool_results'][0]['tool'], 'query_courses')

    def test_counts_are_independent_and_other_owners_hidden(self):
        data = self.ask().json()['tool_results'][0]
        self.assertEqual(data['total'], 5)
        self.assertNotIn(2, [r['id'] for r in data['items']])
        course = data['items'][0]
        self.assertEqual((course['assignment_count'], course['resource_count']), (3, 2))
        self.assertNotIn('source_url', course)
        self.assertNotIn('storage_path', str(data))

    def test_content_sync_and_resource_filters_are_distinct(self):
        for state, expected in [('not_synced', {5, 6}), ('no_resources', {3, 4, 6}),
                                ('has_synced_content', {1, 5})]:
            self.course_plan(content_state=state)
            data = self.ask().json()['tool_results'][0]
            self.assertEqual({r['id'] for r in data['items']}, expected)
        self.assertEqual(data['items'][1]['sync_status'], 'not_synced')
        self.assertEqual(data['items'][1]['local_content_state'], 'has_records')

    def test_course_match_is_distinct_from_empty_content_filter(self):
        self.course_plan(course_name='Database Systems', content_state='no_synced_content')
        data = self.ask().json()['tool_results'][0]
        self.assertEqual((data['matched_course_count'], data['total']), (1, 0))
        self.course_plan(course_id=2)
        data = self.ask().json()['tool_results'][0]
        self.assertEqual((data['matched_course_count'], data['total']), (0, 0))

    def test_school_id_and_display_name_use_authenticated_local_course(self):
        self.course_plan(course_id=11405, course_name='Database Management Systems')
        response = self.ask(query='Database Management Systems（学校课程编号：11405）有没有内容')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['tool_results'][0]['items'][0]['id'], 1)

    def test_alias_matching_uses_owned_candidates(self):
        self.course_plan(course_name='dbms')
        self.planner.invoke.side_effect = [self.planner.invoke.return_value, AIMessage(content='', tool_calls=[
            {'name': 'resolve_course_candidates', 'args': {'indices': [0], 'confident': True}, 'id': 'match1'}])]
        data = self.ask(query='dbms 里有什么').json()['tool_results'][0]
        self.assertEqual([r['id'] for r in data['items']], [1])

    def test_pagination_reports_total_and_has_more(self):
        self.course_plan(limit=2, offset=2)
        data = self.ask().json()['tool_results'][0]
        self.assertEqual((data['total'], len(data['items']), data['has_more']), (5, 2, True))

    def test_clarification_is_a_model_tool_not_empty_document_search(self):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[{'name': 'clarify_school_request',
            'args': {'question': '你指的是课程资源，还是当前知识库中的文档内容？'}, 'id': 'clarify1'}])
        response = self.ask(query='这个里面怎么没有东西')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['answer'], '你指的是课程资源，还是当前知识库中的文档内容？')
        self.llm.invoke.assert_not_called()
        rag.retrieve_documents.assert_not_called()

    def test_invalid_args_auth_and_document_scope_still_apply(self):
        for args in [{'user_id': 2}, {'content_state': 'school_empty'}, {'limit': 100}]:
            self.course_plan(**args)
            self.assertEqual(self.ask().status_code, 502)
        self.course_plan()
        self.assertEqual(self.ask(use_school_tools=False).json()['answer'], 'document answer')
        with patch.object(rag, 'search_document_first', return_value=[]):
            self.assertEqual(self.ask(doc_ids=[1]).json()['answer'], 'document answer')
        self.client.cookies.clear()
        self.assertEqual(self.ask().status_code, 401)


if __name__ == '__main__':
    unittest.main()
