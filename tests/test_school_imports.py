"""School cache -> project copy -> real TXT chunking -> retrieval, with fake embeddings."""
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np
from sqlalchemy import select, func

import test_backend_completion as fixture
from app.models.course_resources import CourseResource
from app.models.documents import Document
from app.models.projects import Project
from app.models.document_child import Child
from app.models.document_parents import Parent
from app.models.workflow_run import WorkflowRun
from app.services import school_downloads, school_imports
from app.routers import school
from app.services import chunk


class SchoolImportTests(unittest.TestCase):
    def setUp(self):
        fixture.BackendCompletionTests.setUp(self)
        self.download_root = self.upload / 'school'
        self.patches.extend([
            patch.object(school_downloads, 'DOWNLOAD_ROOT', self.download_root),
            patch.object(school, 'run_workflow'),
        ])
        for item in self.patches[-2:]:
            item.start()
        self.source = self.download_root / '1' / '1' / '1' / 'cached.txt'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('Database indexes speed up queries. 学校课件中的索引示例。', encoding='utf-8')
        with self.sessions() as db:
            db.add(Project(id=3, user_id=1, name='second owned project'))
            db.add(CourseResource(id=1, course_id=1, source_key='lecture', title='Lecture 1',
                                 resource_type='file', source_url='https://ispace.bnbu.edu.cn/pluginfile.php/1/lecture.txt',
                                 storage_path=str(self.source), content_type='text/plain',
                                 last_synced_at=datetime.now(timezone.utc)))
            db.commit()

    tearDown = fixture.BackendCompletionTests.tearDown

    def post(self, project=1, course=1, resource=1):
        return self.client.post(f'/api/courses/{course}/resources/{resource}/import', json={'project_id': project})

    def test_auth_ownership_and_validation(self):
        for kwargs in ({'project': 2}, {'project': 999}, {'course': 2}, {'resource': 999}):
            self.assertEqual(self.post(**kwargs).status_code, 404)
        self.assertEqual(self.post(project=0).status_code, 422)
        self.client.cookies.clear()
        self.assertEqual(self.post().status_code, 401)
        school.run_workflow.assert_not_called()
        self.assertEqual(list((self.upload / 'project_1').iterdir()), [self.upload / 'project_1' / 'sample.txt'])

    def test_requires_valid_download(self):
        self.source.unlink()
        self.assertEqual(self.post().status_code, 409)
        for field, value, status in [('resource_type', 'folder', 422), ('storage_path', str(self.upload / 'outside.txt'), 409)]:
            with self.sessions() as db:
                row = db.get(CourseResource, 1)
                row.resource_type = 'file'
                setattr(row, field, value)
                db.commit()
            self.assertEqual(self.post().status_code, status)
        school.run_workflow.assert_not_called()

    def test_copy_provenance_deduplication_and_project_scope(self):
        first = self.post()
        self.assertEqual(first.status_code, 202, first.text)
        result = first.json()
        again = self.post()
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()['reused'])
        self.assertEqual(again.json()['document_id'], result['document_id'])
        school.run_workflow.assert_called_once_with(result['job_id'])
        with self.sessions() as db:
            document = db.get(Document, result['document_id'])
            self.assertEqual(document.filename, 'Lecture 1.txt')
            self.assertEqual(Path(document.storage_path).parent, self.upload / 'project_1')
            self.assertEqual(Path(document.storage_path).read_bytes(), self.source.read_bytes())
            run = db.get(WorkflowRun, result['job_id'])
            self.assertEqual(run.request['school_resource_id'], 1)
            self.assertEqual(run.status, 'queued')
            self.assertEqual(db.scalar(select(func.count()).select_from(WorkflowRun)), 1)
        other = self.post(project=3)
        self.assertEqual(other.status_code, 202)
        self.assertNotEqual(other.json()['document_id'], result['document_id'])

    def test_worker_parses_embeds_and_retrieves_then_deletion_allows_reimport(self):
        result = self.post().json()
        model = Mock()
        model.encode.side_effect = lambda texts, **kwargs: np.ones((len(texts), 1024), dtype=np.float32)
        with patch.object(chunk, 'get_embedding_model', return_value=model):
            # Simulate a restart before the response background task executed.
            fixture.durable.recover_pending()
        with self.sessions() as db:
            document = db.get(Document, result['document_id'])
            self.assertEqual(document.status, 'completed')
            self.assertEqual(document.parsed_text, self.source.read_text(encoding='utf-8'))
            self.assertEqual(db.get(WorkflowRun, result['job_id']).status, 'completed')
            self.assertGreater(db.scalar(select(func.count()).select_from(Child)), 0)
            matches = fixture.fixture.search.search_bm25('indexes', 1, db, 100, doc_ids=[document.id])
            self.assertEqual(matches[0]['document_id'], document.id)
            self.assertEqual(fixture.fixture.search.search_bm25('indexes', 2, db, 100), [])
        self.assertEqual(self.post().json()['status'], 'completed')
        response = self.client.delete(f"/api/projects/1/documents/{result['document_id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.source.is_file())
        self.assertEqual(self.post().status_code, 202)

    def test_failed_processing_uses_existing_retry(self):
        result = self.post().json()
        with patch.object(chunk, 'get_embedding_model', side_effect=RuntimeError('model unavailable')):
            fixture.durable.run_workflow(result['job_id'])
        again = self.post()
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['status'], 'failed')
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Parent)), 0)
        retry = self.client.post(f"/api/projects/1/documents/{result['document_id']}/retry")
        self.assertEqual(retry.status_code, 202)
        model = Mock()
        model.encode.side_effect = lambda texts, **kwargs: np.ones((len(texts), 1024), dtype=np.float32)
        with patch.object(chunk, 'get_embedding_model', return_value=model):
            fixture.durable.run_workflow(result['job_id'])
        self.assertEqual(self.post().json()['status'], 'completed')

    def test_commit_failure_removes_copy_but_keeps_school_cache(self):
        from sqlalchemy.orm import Session
        with patch.object(Session, 'commit', side_effect=RuntimeError('commit failed')):
            with self.assertRaisesRegex(RuntimeError, 'commit failed'):
                self.post()
        self.assertTrue(self.source.is_file())
        self.assertEqual(len(list((self.upload / 'project_1').iterdir())), 1)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Document)), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(WorkflowRun)), 0)
        school.run_workflow.assert_not_called()

    def test_partial_copy_failure_cleans_up(self):
        def fail(incoming, outgoing):
            outgoing.write(b'partial')
            raise OSError('disk full')
        with patch.object(school_imports.shutil, 'copyfileobj', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.post()
        self.assertTrue(self.source.is_file())
        self.assertEqual(len(list((self.upload / 'project_1').iterdir())), 1)
        school.run_workflow.assert_not_called()

    def test_lost_commit_response_keeps_committed_file_and_job(self):
        from sqlalchemy.orm import Session
        commit = Session.commit
        def commit_then_disconnect(db):
            commit(db)
            raise RuntimeError('commit acknowledgement lost')
        with patch.object(Session, 'commit', commit_then_disconnect):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement lost'):
                self.post()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['reused'])
        with self.sessions() as db:
            document = db.get(Document, response.json()['document_id'])
            self.assertTrue(Path(document.storage_path).is_file())
            self.assertEqual(db.get(WorkflowRun, response.json()['job_id']).status, 'queued')


if __name__ == '__main__':
    unittest.main()
