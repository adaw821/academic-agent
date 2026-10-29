"""Assignment attachments through real QA graph; school and model traffic are mocked."""
from datetime import timedelta
from pathlib import Path
import json
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage
from sqlalchemy import select, update

import test_school_qa as fixture
from app.models.assignments import Assignment
from app.models.course_resources import CourseResource
from app.models.documents import Document
from app.models.message import Message
from app.models.projects import Project
from app.services import school_downloads
from app.services.durable_workflows import new_run


class SchoolResourceQATests(unittest.TestCase):
    def setUp(self):
        fixture.SchoolQATests.setUp(self)
        self.cache = Path(self.temp.name) / 'school-cache'
        self.patches.append(patch.object(school_downloads, 'DOWNLOAD_ROOT', self.cache))
        self.patches[-1].start()
        now = self.clock.now.return_value
        with self.sessions() as db:
            # Linked file, missing cache, unrelated file, other user's file, submission, disappeared attachment.
            for ident, course, assignment, purpose in [(1, 1, 1, 'assignment_attachment'), (2, 1, 1, 'assignment_attachment'),
                    (3, 1, None, 'unknown'), (4, 2, 3, 'assignment_attachment'),
                    (5, 1, 1, 'student_submission'), (6, 1, 1, 'assignment_attachment')]:
                path = self.cache / '1' / str(course) / str(ident) / 'file.txt'
                if ident == 1:
                    path.parent.mkdir(parents=True)
                    path.write_text('Private attachment body', encoding='utf-8')
                db.add(CourseResource(id=ident, course_id=course, assignment_id=assignment, source_key=str(ident),
                    title=f'Attachment {ident}', purpose=purpose, resource_type='file',
                    source_url=f'https://ispace.bnbu.edu.cn/pluginfile.php/{ident}/mod_assign/intro/file.txt?token=private',
                    storage_path=str(path) if ident in {1, 2} else None,
                    last_synced_at=now - timedelta(days=1) if ident == 6 else now))
            db.add(Project(id=3, user_id=1, name='Other owned project'))
            db.flush()
            # Existing documents 1 and 2 belong to users 1 and 2 respectively.
            new_run(db, 1, document_id=1, request={'school_resource_id': 1})
            new_run(db, 2, document_id=2, request={'school_resource_id': 2})
            db.add(Document(id=3, project_id=3, filename='other.txt', content_type='text/plain', storage_path='private', status='completed'))
            db.flush()
            new_run(db, 3, document_id=3, request={'school_resource_id': 2})
            db.commit()
        self.resource_plan(assignment_name='Lab 1')

    plan = fixture.SchoolQATests.plan
    tearDown = fixture.SchoolQATests.tearDown

    def resource_plan(self, **args):
        self.planner.invoke.return_value = AIMessage(content='', tool_calls=[
            {'name': 'query_assignment_resources', 'args': args, 'id': 'attachment-call'}])

    def ask(self, **kwargs):
        return self.client.post('/api/projects/1/ask', json={'query': 'Lab 1 的题目文件在哪里？', 'use_school_tools': True, **kwargs})

    def test_links_cache_project_scope_and_saved_provenance(self):
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        data = result['tool_results'][0]
        self.assertEqual(data['tool'], 'query_assignment_resources')
        self.assertEqual([r['resource_id'] for r in data['items']], [1, 2, 6])
        self.assertEqual([r['cache_status'] for r in data['items']], ['downloaded', 'not_downloaded', 'not_downloaded'])
        self.assertEqual(data['items'][0]['project_document'], {'document_id': 1, 'status': 'completed'})
        self.assertIsNone(data['items'][1]['project_document'])
        self.assertEqual(data['items'][2]['source_status'], 'not_seen')
        self.assertNotIn(str(self.cache), json.dumps(data))
        self.assertNotIn('Private attachment body', json.dumps(data))
        self.assertNotIn('token=', json.dumps(data))
        self.assertEqual(result['sources'], [])
        fixture.rag.retrieve_documents.assert_not_called()
        with self.sessions() as db:
            saved = db.scalar(select(Message).where(Message.conversation_id == result['conversation_id'], Message.role == 'assistant'))
            self.assertEqual(saved.response_data['tool_results'][0]['total'], 3)

    def test_missing_ambiguous_and_other_user_assignments(self):
        for args, reason in [({}, 'assignment_required'), ({'assignment_id': 3}, 'assignment_not_found'),
                             ({'assignment_name': '%'}, 'assignment_not_found')]:
            self.resource_plan(**args)
            data = self.ask().json()['tool_results'][0]
            self.assertEqual(data['reason'], reason)
            self.assertEqual(data['assignments'], [])
        with self.sessions() as db:
            db.execute(update(Assignment).where(Assignment.id == 2).values(title='Lab 1'))
            db.commit()
        self.resource_plan(assignment_name='Lab 1')
        data = self.ask().json()['tool_results'][0]
        self.assertEqual(data['reason'], 'ambiguous_assignment')
        self.assertEqual({r['id'] for r in data['assignments']}, {1, 2})

    def test_course_filter_and_pagination(self):
        self.resource_plan(assignment_name='lab 1', course_name='database', limit=1, offset=1)
        data = self.ask().json()['tool_results'][0]
        self.assertEqual((data['total'], data['has_more']), (3, True))
        self.assertEqual(data['items'][0]['resource_id'], 2)
        self.resource_plan(assignment_name='Lab 1', course_name='%')
        self.assertEqual(self.ask().json()['tool_results'][0]['reason'], 'assignment_not_found')
        self.resource_plan(assignment_id=2)
        data = self.ask().json()['tool_results'][0]
        self.assertEqual((data['items'], data['total']), ([], 0))

    def test_invalid_parameters_and_explicit_document_scope(self):
        for args in ({'user_id': 2}, {'project_id': 2}, {'assignment_id': True},
                     {'assignment_id': 1, 'assignment_name': 'Lab 1'}, {'limit': 21}, {'offset': -1}):
            self.resource_plan(**args)
            self.assertEqual(self.ask().status_code, 502, args)
        self.llm.invoke.assert_not_called()
        self.resource_plan(assignment_id=1)
        before = self.planner.invoke.call_count
        response = self.ask(doc_ids=[1])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['answer'], 'document answer')
        self.assertEqual(self.planner.invoke.call_count, before)

    def test_invalid_cache_and_source_do_not_expose_paths(self):
        with self.sessions() as db:
            db.execute(update(CourseResource).where(CourseResource.id == 1).values(
                storage_path=str(Path(self.temp.name) / 'outside.txt'), source_url='https://example.com/secret'))
            db.execute(update(Document).where(Document.id == 1).values(status='failed'))
            db.commit()
        data = self.ask().json()['tool_results'][0]
        self.assertEqual(data['items'][0]['cache_status'], 'unavailable')
        self.assertIsNone(data['items'][0]['source_url'])
        self.assertEqual(data['items'][0]['project_document']['status'], 'failed')
        self.assertNotIn('outside.txt', str(data))

    def test_durable_attachment_query_restores_result(self):
        durable = fixture.fixture.durable
        job = self.client.post('/api/projects/1/ask/jobs', json={
            'query': 'Lab 1 的附件在哪里？', 'use_school_tools': True}).json()
        with patch.object(durable.Checkpoints, 'finish', side_effect=RuntimeError('interrupted')):
            durable.run_workflow(job['job_id'])
        self.assertEqual(self.client.post(f"/api/projects/1/jobs/{job['job_id']}/retry").status_code, 202)
        durable.run_workflow(job['job_id'])
        self.planner.invoke.assert_called_once()
        self.llm.invoke.assert_called_once()
        result = self.client.get(f"/api/projects/1/jobs/{job['job_id']}").json()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result']['tool_results'][0]['tool'], 'query_assignment_resources')


if __name__ == '__main__':
    unittest.main()
