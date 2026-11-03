import importlib
import os
from pathlib import Path
import pkgutil
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DATABASE_URL', 'sqlite://')
import app.models
for module in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module('app.models.' + module.name)
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.db.database import Base, get_db
from app.models.users import User
from app.models.user_session import UserSession
from app.models.email_code import EmailCode, EmailRate
from app.routers.user_routers import router, password_hasher
from app.services import email_auth


class EmailAuthTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, dict(SMTP_HOST='smtp.example.test', SMTP_USER='sender', SMTP_PASSWORD='secret', SMTP_FROM='sender@example.test', EMAIL_CODE_SECRET='x' * 32))
        self.env.start()
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine, tables=[User.__table__, UserSession.__table__, EmailCode.__table__, EmailRate.__table__])
        self.sessions = sessionmaker(bind=self.engine)
        def database():
            with self.sessions() as db:
                yield db
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.factory = patch.object(email_auth, 'SessionLocal', self.sessions)
        self.factory.start()
        self.mail = patch.object(email_auth, 'send_mail')
        self.send = self.mail.start()

    def tearDown(self):
        self.client.close()
        self.mail.stop()
        self.factory.stop()
        self.engine.dispose()
        self.env.stop()

    def challenge(self, purpose, expires=None):
        with self.sessions() as db:
            db.add(EmailCode(email='a@example.test', purpose=purpose, digest=email_auth.digest('a@example.test', purpose, '123456'), expires=expires or int(time.time()) + 600, attempts=0))
            db.commit()

    def create(self):
        return self.client.post('/user/create', json=dict(username='Alice', email='A@example.test', password='password123', code='123456'))

    def test_registration_requires_code_and_consumes_it(self):
        self.assertEqual(self.create().status_code, 400)
        self.challenge('register')
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create().status_code, 400)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(User.email)), 'a@example.test')
            self.assertIsNone(db.get(EmailCode, ('a@example.test', 'register')))

    def test_wrong_attempt_limit_expiry_and_purpose(self):
        self.challenge('reset')
        self.assertEqual(self.create().status_code, 400)
        for _ in range(5):
            self.assertEqual(self.client.post('/user/reset-password', json=dict(email='a@example.test', password='newpassword', code='000000')).status_code, 400)
        with self.sessions() as db:
            self.assertEqual(db.get(EmailCode, ('a@example.test', 'reset')).attempts, 5)
        self.assertEqual(self.client.post('/user/reset-password', json=dict(email='a@example.test', password='newpassword', code='123456')).status_code, 400)
        self.challenge('register', int(time.time()) - 1)
        self.assertEqual(self.create().status_code, 400)

    def test_reset_invalidates_sessions_and_old_password(self):
        self.challenge('register')
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.client.post('/user/login', json=dict(account='a@example.test', password='password123')).status_code, 200)
        self.challenge('reset')
        payload = dict(email='a@example.test', password='newpassword', code='123456')
        self.assertEqual(self.client.post('/user/reset-password', json=payload).status_code, 200)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(UserSession)), 0)
        self.assertEqual(self.client.post('/user/reset-password', json=payload).status_code, 400)
        self.assertEqual(self.client.post('/user/login', json=dict(account='a@example.test', password='password123')).status_code, 401)
        self.assertEqual(self.client.post('/user/login', json=dict(account='A@example.test', password='newpassword')).status_code, 200)

    def test_delivery_is_generic_throttled_and_hashed(self):
        with patch.object(email_auth.secrets, 'randbelow', return_value=123456):
            result = self.client.post('/user/email-code', json=dict(email='a@example.test', purpose='register'))
        self.assertEqual(result.status_code, 202)
        self.assertNotIn('123456', result.text)
        self.send.assert_called_once()
        with self.sessions() as db:
            self.assertNotIn('123456', db.get(EmailCode, ('a@example.test', 'register')).digest)
        self.assertEqual(self.client.post('/user/email-code', json=dict(email='a@example.test', purpose='reset')).status_code, 429)
        missing = self.client.post('/user/email-code', json=dict(email='missing@example.test', purpose='reset'))
        self.assertEqual(missing.json(), result.json())
        self.send.assert_called_once()

    def test_smtp_failure_does_not_issue_code_and_missing_config_fails(self):
        self.send.side_effect = RuntimeError('private SMTP detail')
        result = self.client.post('/user/email-code', json=dict(email='a@example.test', purpose='register'))
        self.assertEqual(result.status_code, 202)
        with self.sessions() as db:
            self.assertIsNone(db.get(EmailCode, ('a@example.test', 'register')))
        with patch.dict(os.environ, {'SMTP_PASSWORD': ''}):
            self.assertEqual(self.client.post('/user/email-code', json=dict(email='b@example.test', purpose='register')).status_code, 503)

    def test_existing_phone_login_does_not_require_email(self):
        with self.sessions() as db:
            db.add(User(username='Legacy', phone='13800000000', password_hash=password_hasher.hash('password123')))
            db.commit()
        self.assertEqual(self.client.post('/user/login', json=dict(account='13800000000', password='password123')).status_code, 200)

    def test_optional_phone_is_saved_and_supports_login(self):
        self.challenge('register')
        payload = dict(username='Alice', email='a@example.test', password='password123', code='123456', phone=' 13800138000 ')
        result = self.client.post('/user/create', json=payload)
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()['phone'], '13800138000')
        self.assertEqual(self.client.post('/user/login', json=dict(account='13800138000', password='password123')).status_code, 200)

    def test_invalid_and_duplicate_phone_can_be_corrected_to_blank(self):
        with self.sessions() as db:
            db.add(User(username='Legacy', phone='13800138000', password_hash=password_hasher.hash('password123')))
            db.commit()
        self.challenge('register')
        payload = dict(username='Alice', email='a@example.test', password='password123', code='123456', phone='not-a-phone')
        self.assertEqual(self.client.post('/user/create', json=payload).status_code, 422)
        payload['phone'] = '13800138000'
        self.assertEqual(self.client.post('/user/create', json=payload).status_code, 409)
        payload['phone'] = '  '
        result = self.client.post('/user/create', json=payload)
        self.assertEqual(result.status_code, 201)
        self.assertIsNone(result.json()['phone'])

    def test_migration_creates_and_removes_only_new_tables(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect
        spec = importlib.util.spec_from_file_location('email_migration', Path(__file__).resolve().parents[1] / 'migration/versions/c92e81f03a25_email_verification.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as conn:
            EmailCode.__table__.drop(conn)
            EmailRate.__table__.drop(conn)
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                for model in (EmailCode, EmailRate):
                    self.assertEqual(set(model.__table__.columns.keys()), {c['name'] for c in inspect(conn).get_columns(model.__tablename__)})
                migration.downgrade()
            self.assertTrue(inspect(conn).has_table('users'))
            self.assertFalse(inspect(conn).has_table('email_codes'))


if __name__ == '__main__':
    unittest.main()
