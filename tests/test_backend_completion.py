"""Real authenticated HTTP/SQLite transactions, mocked external inference only.

Run independently because legacy tests install module stubs at import time.
"""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
POSTGRES_URL = os.environ.get('DATABASE_URL') if os.environ.get('BACKEND_TEST_POSTGRES') == '1' else None
os.environ['DATABASE_URL'] = 'sqlite://'
for model in ('users', 'projects', 'documents', 'document_parents', 'document_child',
              'conversation', 'message', 'user_session', 'courses', 'course_timetable'):
    importlib.import_module('app.models.' + model)
import numpy
from docling_core.types.doc import DoclingDocument, ImageRef
import test_document_scope as fixture  # Existing isolated model-service imports.
sys.modules['app.routers.rag_flow'] = fixture.rag
sys.modules['app.services.search_services'] = fixture.search
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, update, func
from sqlalchemy.orm import sessionmaker
from app.db.database import Base, get_db
from app.models.users import User
from app.models.projects import Project
from app.models.documents import Document
from app.models.document_parents import Parent
from app.models.document_child import Child
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user_session import UserSession
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.models.assignments import Assignment
from app.models.workflow_run import WorkflowRun
from app.routers import library, school
from app.services import durable_workflows as durable
from app.services.ispace import SchoolLoginRequired
from app.services.search_services import history_messages_control
import hashlib


@event.listens_for(UserSession, "load")
def sqlite_timezone(value, context):
    if context.session.get_bind().dialect.name == "sqlite" and value.expires_at.tzinfo is None:
        value.expires_at = value.expires_at.replace(tzinfo=timezone.utc)


class BackendCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.upload = root / 'uploads'
        self.schema = None
        if POSTGRES_URL:
            self.schema = 'backend_test_' + uuid4().hex
            self.engine = create_engine(POSTGRES_URL, execution_options={'schema_translate_map': {None: self.schema}})
            with self.engine.begin() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA {self.schema}')
        else:
            self.engine = create_engine('sqlite:///' + (root / 'test.db').as_posix(),
                                        connect_args={'check_same_thread': False})
            @event.listens_for(self.engine, 'connect')
            def foreign_keys(connection, _):
                connection.execute('PRAGMA foreign_keys=ON')
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.patches = [patch.object(durable, 'SessionLocal', self.sessions),
                        patch.object(library, 'UPLOAD_ROOT', self.upload),
                        patch.object(library, 'run_workflow'),
                        patch.dict(os.environ, {'ISPACE_SESSION_DIR': str(root / 'sessions')})]
        for item in self.patches:
            item.start()
        with self.sessions() as db:
            for ident in (1, 2):
                db.add(User(id=ident, username=f'u{ident}', email=f'u{ident}@test', password_hash='unused'))
            db.flush()
            for ident in (1, 2):
                db.add(Project(id=ident, user_id=ident, name=f'p{ident}'))
                db.add(Course(id=ident, user_id=ident, course_name=f'c{ident}'))
                db.add(UserSession(token_hash=hashlib.sha256(f'token{ident}'.encode()).hexdigest(),
                                   user_id=ident, expires_at=datetime.now(timezone.utc)+timedelta(days=1)))
            db.flush()
            for ident in (1, 2):
                path = self.upload / f'project_{ident}' / 'sample.txt'
                path.parent.mkdir(parents=True)
                path.write_text('complete document', encoding='utf-8')
                db.add(Document(id=ident, project_id=ident, filename='sample.txt', content_type='text/plain',
                                storage_path=str(path), status='completed', parsed_text='complete document'))
                db.add(CourseSchedule(course_id=ident, day_of_week='Monday', start_time='09:00',
                                      end_time='10:00', location='room'))
            db.commit()
            self.conversation_id = history_messages_control('answer', 'question', None, db, 1,
                response_data={'answer': 'answer', 'sources': [{'document_id': 1}], 'retrieved_contexts': ['evidence']})
        app = FastAPI()
        app.include_router(library.router, prefix='/api')
        app.include_router(school.router, prefix='/api')
        def dependency():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = dependency
        self.client = TestClient(app)
        self.client.cookies.set('session_id', 'token1')

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        if self.schema:
            with self.engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA {self.schema} CASCADE')
        self.engine.dispose()
        self.temp.cleanup()

    def test_history_pagination_provenance_and_isolation(self):
        response = self.client.get('/api/projects/1/conversations')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['items'][0]['message_count'], 2)
        response = self.client.get(f'/api/projects/1/conversations/{self.conversation_id}/messages?offset=1&limit=1')
        self.assertEqual(response.json()['items'][0]['response_data']['retrieved_contexts'], ['evidence'])
        self.assertEqual(self.client.get('/api/projects/2/conversations').status_code, 404)
        self.assertEqual(self.client.get('/api/projects/1/conversations/999/messages').status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/projects/1/conversations').status_code, 401)

    def test_content_download_legacy_and_path_guard(self):
        self.assertEqual(self.client.get('/api/projects/1/documents/1/content').json()['content'], 'complete document')
        response = self.client.get('/api/projects/1/documents/1/file')
        self.assertEqual(response.text, 'complete document')
        self.assertIn('attachment', response.headers['content-disposition'])
        self.assertEqual(self.client.get('/api/projects/1/documents/2/content').status_code, 404)
        with self.sessions() as db:
            doc = db.get(Document, 1)
            doc.parsed_text = None
            doc.storage_path = str(Path(self.temp.name) / 'outside.txt')
            db.commit()
        self.assertEqual(self.client.get('/api/projects/1/documents/1/content').status_code, 409)
        self.assertEqual(self.client.get('/api/projects/1/documents/1/file').status_code, 409)
        self.assertEqual(self.client.delete('/api/projects/1/documents/1').status_code, 409)

    def test_delete_cascades_and_removes_assets(self):
        with self.sessions() as db:
            parent = Parent(document_id=1, content='body', chunk_index=0)
            db.add(parent); db.flush()
            db.add(Child(parent_id=parent.id, content='body', chunk_index=0, embedding=[0.] * 1024))
            run = durable.new_run(db, 1, document_id=1)
            run.status = 'completed'
            db.commit()
        images = self.upload / 'project_1' / 'sample.images'
        images.mkdir(); (images / 'sample.png').write_bytes(b'pixels')
        response = self.client.delete('/api/projects/1/documents/1')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['cleanup_pending'])
        self.assertFalse(images.exists())
        self.assertFalse((images.parent / 'sample.txt').exists())
        with self.sessions() as db:
            self.assertIsNone(db.get(Document, 1))
            self.assertEqual(db.scalar(select(func.count()).select_from(Child)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(WorkflowRun)), 0)
        self.assertEqual(self.client.get(f'/api/projects/1/conversations/{self.conversation_id}/messages').status_code, 200)

    def test_document_retry_and_active_job_guard(self):
        self.assertEqual(self.client.post('/api/projects/1/documents/1/retry').status_code, 409)
        response = self.client.post('/api/projects/1/documents/1/retry?restart=true')
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.client.get('/api/projects/1/jobs').json()['total'], 1)
        self.assertEqual(self.client.post('/api/projects/1/documents/1/retry').status_code, 409)
        self.assertEqual(self.client.delete('/api/projects/1/documents/1').status_code, 409)

    def test_school_connection_never_echoes_secret(self):
        with patch.object(school, 'ISpaceClient') as client:
            client.return_value.__enter__.return_value.discover_courses.return_value = {'courses': [], 'complete': False}
            response = self.client.put('/api/school/session', json={'session': 'sensitive-token'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('sensitive-token', response.text)
        self.assertEqual(self.client.get('/api/school/session').json()['configured'], True)
        self.client.cookies.set('session_id', 'token2')
        self.assertFalse(self.client.get('/api/school/session').json()['configured'])
        self.client.cookies.set('session_id', 'token1')
        response = self.client.put('/api/school/session', json={'session': 'secret;invalid'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('secret', response.text)
        with patch.object(school, 'ISpaceClient') as client:
            client.return_value.__enter__.return_value.discover_courses.side_effect = SchoolLoginRequired('secret')
            self.assertEqual(self.client.put('/api/school/session', json={'session': 'replacement'}).status_code, 409)
        self.assertEqual(school.load_school_session(1), 'sensitive-token')
        self.assertEqual(self.client.delete('/api/school/session').status_code, 204)
        self.assertFalse(self.client.get('/api/school/session').json()['configured'])

    def test_saved_timetable_and_courses_are_user_scoped(self):
        result = self.client.get('/api/timetable').json()
        self.assertEqual([x['course_id'] for x in result['items']], [1])
        self.assertEqual(self.client.get('/api/timetable?course_id=2').json()['items'], [])
        self.assertEqual(self.client.get('/api/courses').json()['total'], 1)
        self.assertEqual(self.client.get('/api/courses/2/resources').status_code, 404)
        self.assertEqual(self.client.post('/api/courses/sync', json={'source_url': 'https://evil.test/x'}).status_code, 422)

    def test_qa_recovery_reuses_answer_and_commits_messages_once(self):
        response = self.client.post('/api/projects/1/ask/jobs', json={'query': 'question', 'doc_ids': [1]})
        self.assertEqual(response.status_code, 202, response.text)
        run_id = response.json()['job_id']
        rag = fixture.rag
        with patch.object(rag, 'get_history_messages', return_value=([], '', None)), \
             patch.object(rag, 'rewrite_query', return_value='question'), \
             patch.object(rag, 'translate_query', return_value=None), \
             patch.object(rag, 'retrieve_documents', return_value=([], [])), \
             patch.object(rag, 'rerank_results', return_value=[]), \
             patch.object(rag, 'generate_answer', return_value={'answer': 'recovered', 'sources': [], 'retrieved_contexts': []}) as answer:
            with patch.object(durable.Checkpoints, 'finish', side_effect=RuntimeError('failed after message flush')):
                durable.run_workflow(run_id)
            with self.sessions() as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 2)
            self.assertEqual(self.client.get(f'/api/projects/1/jobs/{run_id}').json()['status'], 'failed')
            self.assertEqual(self.client.post(f'/api/projects/1/jobs/{run_id}/retry').status_code, 202)
            durable.run_workflow(run_id)
            durable.run_workflow(run_id)
            answer.assert_called_once()
        result = self.client.get(f'/api/projects/1/jobs/{run_id}').json()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['attempts'], 2)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 4)
            self.assertEqual(db.get(WorkflowRun, run_id).checkpoints, {})
        self.assertEqual(self.client.get(f'/api/projects/2/jobs/{run_id}').status_code, 404)

    def test_expired_lease_resumes_document_without_duplicate_partial_writes(self):
        with self.sessions() as db:
            run = durable.new_run(db, 1, document_id=1)
            run.status, run.lease_token = 'running', 'abandoned'
            run.lease_until = datetime.now(timezone.utc) - timedelta(minutes=5)
            run_id = run.id
            db.get(Document, 1).status = 'processing'
            db.commit()
        mock_chunk = types.ModuleType('app.services.chunk')
        def chunk(content, db, document_id, filename, **kwargs):
            db.add(Parent(document_id=document_id, content='new', chunk_index=0))
            db.get(Document, document_id).parsed_text = 'new'
        mock_chunk.chunking = Mock(side_effect=chunk)
        with patch.dict(sys.modules, {'app.services.chunk': mock_chunk}):
            durable.recover_pending()
            durable.run_workflow(run_id)
        mock_chunk.chunking.assert_called_once()
        with self.sessions() as db:
            self.assertEqual(db.get(Document, 1).status, 'completed')
            self.assertEqual(db.scalar(select(func.count()).select_from(Parent)), 1)
            self.assertEqual(db.get(WorkflowRun, run_id).status, 'completed')

    def test_image_failure_summary_is_persisted_and_exposed_for_both_outcomes(self):
        self.client.app.include_router(fixture.rag.router, prefix="/api")
        from app.services.image_failures import TooManyImageFailures
        summary = {'total': 3, 'failed': 3, 'failures': [{'picture_id': '#/pictures/0', 'pages': [2], 'reason': '识别服务限流'}]}
        with self.sessions() as db:
            run = durable.new_run(db, 1, document_id=1)
            db.commit()
            run_id = run.id
        module = types.ModuleType('app.services.chunk')
        module.chunking = Mock(side_effect=TooManyImageFailures(summary))
        with patch.dict(sys.modules, {'app.services.chunk': module}):
            durable.run_workflow(run_id)
        result = self.client.get('/api/projects/1/documents').json()[0]
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['image_summary'], summary)
        self.assertEqual(self.client.post('/api/projects/1/documents/1/retry').status_code, 202)
        summary = dict(summary, failed=1)
        module.chunking = Mock(return_value=summary)
        with patch.dict(sys.modules, {'app.services.chunk': module}):
            durable.run_workflow(run_id)
        result = self.client.get('/api/projects/1/documents').json()[0]
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['image_summary'], summary)

    def test_lost_lease_cannot_commit_domain_changes(self):
        with self.sessions() as db:
            run = durable.new_run(db, 1)
            run.status, run.lease_token = 'running', 'new-owner'
            run.lease_until = datetime.now(timezone.utc) + timedelta(minutes=2)
            db.commit(); run_id = run.id
        with self.sessions() as db:
            db.add(Parent(document_id=1, content='stale', chunk_index=0))
            with self.assertRaises(durable.LeaseLost):
                durable.Checkpoints(run_id, 'old-owner', {}).finish(db, {})
            db.rollback()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Parent)), 0)

    def test_docling_checkpoint_preserves_image_pixels(self):
        from docling_core.types.doc import DoclingDocument, ImageRef
        from PIL import Image
        document = DoclingDocument(name='test')
        document.add_picture(image=ImageRef.from_pil(Image.new('RGB', (4, 4), 'red'), dpi=72))
        restored = durable.decode(durable.encode({'document': document}))['document']
        self.assertEqual(restored.pictures[0].get_image(restored).getpixel((0, 0)), (255, 0, 0))

    def test_staged_delete_recovers_after_rollback_and_cleans_after_commit(self):
        source = self.upload / 'project_1' / 'sample.txt'
        trash = self.upload / '.trash' / 'interrupted'
        trash.mkdir(parents=True)
        source.rename(trash / source.name)
        (trash / 'manifest.json').write_text(json.dumps({'project_id': 1, 'document_id': 1,
                                                       'files': [source.name]}), encoding='utf-8')
        durable.recover_deleted_files()
        self.assertTrue(source.is_file())
        self.assertFalse(trash.exists())
        with patch.object(library.shutil, 'rmtree', side_effect=OSError('busy')):
            response = self.client.delete('/api/projects/1/documents/1')
        self.assertTrue(response.json()['cleanup_pending'])
        durable.recover_deleted_files()
        self.assertEqual(list((self.upload / '.trash').iterdir()), [])

    def test_failed_delete_commit_restores_original_file(self):
        app = self.client.app
        previous = app.dependency_overrides[get_db]
        def fail_commit():
            with self.sessions() as db:
                db.commit = Mock(side_effect=RuntimeError('commit failed'))
                yield db
        app.dependency_overrides[get_db] = fail_commit
        try:
            with self.assertRaisesRegex(RuntimeError, 'commit failed'):
                self.client.delete('/api/projects/1/documents/1')
        finally:
            app.dependency_overrides[get_db] = previous
        self.assertTrue((self.upload / 'project_1' / 'sample.txt').is_file())
        self.assertEqual(self.client.get('/api/projects/1/documents/1/content').status_code, 200)

    def test_document_resume_skips_decode_and_rolls_back_partial_chunks(self):
        with self.sessions() as db:
            run = durable.new_run(db, 1, document_id=1)
            run_id = run.id
            db.get(Document, 1).status = 'queued'
            db.commit()
        from app.services.workflow_graphs import build_document_graph
        decode = Mock(return_value={'text': 'body'})
        fail = [True]
        def chunk(content, db, document_id, filename, **kwargs):
            def split(state):
                db.add(Parent(document_id=document_id, content=state['text'], chunk_index=0))
                db.flush()
                if fail[0]:
                    raise RuntimeError('interrupted before final commit')
                db.get(Document, document_id).parsed_text = state['text']
                return {}
            nodes = {'decode': decode, 'convert': lambda s: {}, 'extract_images': lambda s: {},
                     'images_type_router': lambda s: {}, 'enrich_images': lambda s: {},
                     'export': lambda s: {}, 'split_embed_store': split}
            nodes = kwargs['checkpoints'].wrap(nodes, uncached={'split_embed_store'})
            build_document_graph(nodes, is_txt=True, needs_images=False).invoke({})
        module = types.ModuleType('app.services.chunk'); module.chunking = chunk
        with patch.dict(sys.modules, {'app.services.chunk': module}):
            durable.run_workflow(run_id)
            with self.sessions() as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(Parent)), 0)
            self.assertEqual(self.client.post('/api/projects/1/documents/1/retry').status_code, 202)
            fail[0] = False
            durable.run_workflow(run_id)
        decode.assert_called_once()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Parent)), 1)
            self.assertEqual(db.get(Document, 1).status, 'completed')

    def test_concurrent_workers_claim_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        with self.sessions() as db:
            run = durable.new_run(db, 1, request={'query': 'question'})
            run_id = run.id
            db.commit()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def execute(project_id, request, db, user, *, checkpoints, local_browser_allowed=False):
            calls.append(project_id)
            entered.set()
            release.wait(10)
            checkpoints.finish(db, {'answer': 'once'})
        with patch.object(fixture.rag, 'execute_qa', side_effect=execute), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(durable.run_workflow, run_id)
            try:
                self.assertTrue(entered.wait(5))
                executor.submit(durable.run_workflow, run_id).result(timeout=5)
            finally:
                release.set()
            first.result(timeout=5)
        self.assertEqual(calls, [1])
        with self.sessions() as db:
            self.assertEqual(db.get(WorkflowRun, run_id).attempts, 1)
            self.assertEqual(db.get(WorkflowRun, run_id).status, 'completed')


if __name__ == '__main__':
    unittest.main()
