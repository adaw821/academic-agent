"""Real PostgreSQL/model smoke test using an isolated schema and synthetic data.

No school login or school requests. --document also exercises cached-file import,
real TXT parsing/embedding/retrieval and a durable QA job. Never starts app recovery.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document', action='store_true')
    parser.add_argument('--skip-tools', action='store_true', help='Skip school-tool model calls when rerunning document checks')
    parser.add_argument('--report', type=Path, default=Path('.local/backend-live-validation.json'))
    args = parser.parse_args()
    if args.skip_tools and not args.document:
        parser.error('--skip-tools requires --document')
    # Use existing local weights only; this command never starts large model downloads.
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'school_access': 'not_attempted',
              'requested_tools': not args.skip_tools, 'requested_document': args.document,
              'data': 'synthetic_isolated_schema', 'stages': []}
    engine = None
    created = False
    schema = 'backend_live_' + uuid4().hex

    def stage(name, action):
        print(json.dumps({'stage': name, 'status': 'running'}), flush=True)
        started = perf_counter()
        try:
            details = action() or {}
            row = dict(stage=name, status='passed', **details)
        except Exception as exc:
            row = dict(stage=name, status='failed', error_type=type(exc).__name__)
            if hasattr(exc, 'status_code'):
                row['status_code'] = exc.status_code
        row['seconds'] = round(perf_counter() - started, 2)
        report['stages'].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return row['status'] == 'passed'

    try:
        from sqlalchemy import create_engine, select, func
        from sqlalchemy.orm import sessionmaker
        from fastapi.testclient import TestClient
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from main import app
        from app.db.database import Base, get_db
        from app.models.users import User
        from app.models.user_session import UserSession
        from app.models.projects import Project
        from app.models.courses import Course
        from app.models.course_timetable import CourseSchedule
        from app.models.assignments import Assignment
        from app.models.course_resources import CourseResource
        from app.models.deadline_reminder import DeadlineReminder
        from app.models.documents import Document
        from app.models.document_child import Child
        from app.models.document_parents import Parent
        from app.models.workflow_run import WorkflowRun
        from app.services import answer_services, search_services, context_management, durable_workflows, school_downloads
        from app.services.deadline_reminders import generate_deadline_reminders
        from app.routers import library, rag_flow

        engine = create_engine(os.environ['DATABASE_URL'], connect_args={'connect_timeout': 5},
                               execution_options={'schema_translate_map': {None: schema}})
        if engine.dialect.name != 'postgresql':
            raise ValueError('PostgreSQL required')
        with engine.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        created = True
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)

        def migration_check():
            DeadlineReminder.__table__.drop(engine)
            path = Path(__file__).resolve().parents[1] / 'migration/versions/e6a219db503c_deadline_reminders.py'
            spec = importlib.util.spec_from_file_location('live_reminder_migration', path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with engine.begin() as conn:
                conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}", public')
                with Operations.context(MigrationContext.configure(conn)):
                    migration.upgrade()
                    migration.downgrade()
                    migration.upgrade()
            return {'upgrade_downgrade_upgrade': True}

        if not stage('postgres_reminder_migration', migration_check):
            raise RuntimeError('migration failed')

        with tempfile.TemporaryDirectory(prefix='academic-backend-live-') as folder, ExitStack() as stack:
            root = Path(folder)
            stack.enter_context(patch.object(library, 'UPLOAD_ROOT', root / 'uploads'))
            stack.enter_context(patch.object(school_downloads, 'DOWNLOAD_ROOT', root / 'school'))
            stack.enter_context(patch.object(durable_workflows, 'SessionLocal', sessions))
            # Real model calls, bounded timeout and no retries; no mock model responses.
            bounded = answer_services.llm.model_copy(update={'request_timeout': 60, 'max_retries': 0})
            for module in (answer_services, search_services, context_management):
                stack.enter_context(patch.object(module, 'llm', bounded))
            token = uuid4().hex
            now = datetime.now(timezone.utc)
            with sessions() as db:
                user = User(username='live-validation', email='validation@example.invalid', password_hash='not-a-login-password')
                db.add(user)
                db.flush()
                user_id = user.id
                project = Project(user_id=user_id, name='Synthetic live validation')
                course = Course(user_id=user_id, course_name='数据库验收课程', teachers='测试教师', last_synced_at=now)
                db.add_all([project, course])
                db.flush()
                project_id, course_id = project.id, course.id
                assignment = Assignment(course_id=course_id, external_activity_id='synthetic-lab', title='验收实验一',
                    source_url='https://ispace.bnbu.edu.cn/mod/assign/view.php?id=99999999',
                    due_at=now + timedelta(hours=6), submission_status='No submissions have been made yet', last_synced_at=now)
                db.add(assignment)
                db.flush()
                resource = CourseResource(course_id=course_id, assignment_id=assignment.id, source_key='synthetic',
                    title='验收实验说明.txt', resource_type='file', purpose='assignment_attachment',
                    source_url='https://ispace.bnbu.edu.cn/pluginfile.php/99999999/mod_assign/intro/test.txt', last_synced_at=now)
                db.add(resource)
                db.flush()
                resource_id = resource.id
                cache = root / 'school' / str(user_id) / str(course_id) / str(resource_id) / 'fixture.txt'
                cache.parent.mkdir(parents=True)
                cache.write_text('数据库验收实验一。唯一验收标识为 ORBIT-742。提交文件必须命名为 db_lab_report.pdf。报告必须包含事务隔离级别对比和两条 SQL 查询。', encoding='utf-8')
                resource.storage_path = str(cache)
                resource.content_type = 'text/plain'
                db.add(CourseSchedule(course_id=course_id, day_of_week='Mon', start_time='09:00', end_time='10:00', location='测试楼 A101'))
                db.add(UserSession(user_id=user_id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=now + timedelta(hours=2)))
                db.commit()

            def concurrent_reminders():
                def generate():
                    with sessions() as db:
                        count = generate_deadline_reminders(db, user_id)
                        db.commit()
                        return count
                with ThreadPoolExecutor(max_workers=2) as workers:
                    counts = sorted(workers.map(lambda _: generate(), range(2)))
                assert counts == [0, 1]
                return {'concurrent_connections': 2, 'created_counts': counts}

            if not stage('postgres_concurrent_reminder_generation', concurrent_reminders):
                raise RuntimeError('concurrent reminder generation failed')

            def dependency():
                with sessions() as db:
                    yield db

            previous = dict(app.dependency_overrides)
            stack.callback(lambda: (app.dependency_overrides.clear(), app.dependency_overrides.update(previous)))
            app.dependency_overrides[get_db] = dependency
            # No lifespan context: do not run recovery against the configured application DB.
            client = TestClient(app, raise_server_exceptions=True)
            stack.callback(client.close)
            client.cookies.set('session_id', token)

            def tool_check(query, expected):
                response = client.post(f'/api/projects/{project_id}/ask', json={'query': query, 'use_school_tools': True})
                response.raise_for_status()
                result = response.json()
                assert result['tool_results'][0]['tool'] == expected
                assert result['tool_results'][0]['total'] == 1
                assert result['answer'].strip()
                return {'tool': expected, 'rows': 1, 'answer_characters': len(result['answer'])}

            tool_cases = [
                ('我有哪些未提交的作业？', 'query_assignments'),
                ('验收实验一有哪些关联附件，已经下载了吗？', 'query_assignment_resources'),
                ('我的站内未读截止提醒有哪些？', 'query_deadline_reminders'),
                ('我周一几点上课，在哪上？', 'query_timetable'),
            ]
            for question, tool in ([] if args.skip_tools else tool_cases):
                stage(tool, lambda question=question, tool=tool: tool_check(question, tool))

            if args.document:
                document_ids = []

                def import_check():
                    response = client.post(f'/api/courses/{course_id}/resources/{resource_id}/import', json={'project_id': project_id})
                    response.raise_for_status()
                    info = response.json()
                    document_ids.append(info['document_id'])
                    with sessions() as db:
                        document = db.get(Document, info['document_id'])
                        run = db.get(WorkflowRun, info['job_id'])
                        assert document.status == 'completed' and run.status == 'completed'
                        count = db.scalar(select(func.count()).select_from(Child).join(Parent, Parent.id == Child.parent_id)
                                          .where(Parent.document_id == document.id))
                        assert count > 0 and 'ORBIT-742' in document.parsed_text
                    return {'file_type': 'txt', 'chunks': count, 'real_embeddings': True}

                if stage('cached_fixture_import_and_embedding', import_check):
                    def document_check():
                        response = client.post(f'/api/projects/{project_id}/ask/jobs', json={
                            'query': '验收实验的唯一验收标识是什么？提交文件应该叫什么名字？', 'doc_ids': document_ids})
                        response.raise_for_status()
                        info = client.get(f"/api/projects/{project_id}/jobs/{response.json()['job_id']}").json()
                        assert info['status'] == 'completed'
                        result = info['result']
                        assert 'ORBIT-742' in result['answer'] and 'db_lab_report.pdf' in result['answer']
                        assert result['sources'] and result['retrieved_contexts']
                        return {'answer_contains_fixture_facts': True, 'sources': len(result['sources']),
                                'real_embedding_reranker_and_llm': True}
                    stage('durable_document_qa', document_check)
    except Exception as exc:
        report['fatal_error_type'] = type(exc).__name__
    finally:
        if engine is not None:
            if created:
                try:
                    assert re.fullmatch(r'backend_live_[0-9a-f]{32}', schema)
                    with engine.begin() as conn:
                        conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
                    report['isolated_schema_removed'] = True
                except Exception as exc:
                    report['cleanup_error_type'] = type(exc).__name__
                    report['remaining_test_schema'] = schema
            engine.dispose()
        report['passed'] = bool(report['stages']) and all(r['status'] == 'passed' for r in report['stages']) and not any(
            key in report for key in ('fatal_error_type', 'cleanup_error_type'))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'passed': report['passed'], 'report': str(args.report)}, ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
