import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from test_email_auth import Base, User, create_engine, sessionmaker
from app.services import school_onboarding as service
from app.services.school_crypto import seal, unseal
from app.services.ispace import SchoolLoginRequired
from app.services.school_auth import SchoolAuthenticationError


class CredentialLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine, tables=[User.__table__])
        self.sessions = sessionmaker(bind=self.engine)
        self.key = patch('app.services.school_crypto.cipher', return_value=AESGCM(os.urandom(32)))
        self.key.start()
        self.addCleanup(self.key.stop)
        self.factory = patch.object(service, 'SessionLocal', self.sessions)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.data = dict(account='student', password='school-secret', cookie='old', storage_state={})
        with self.sessions() as db:
            db.add(User(id=1, username='test', email='test@example.test', password_hash='unused', school_credentials=seal(1, self.data)))
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_expiry_renews_once_and_saves_encrypted_state(self):
        with patch.object(service, 'ISpaceClient') as client, patch.object(service, 'authenticate', return_value=dict(self.data, cookie='new')) as auth, patch('app.services.school_sessions.save_school_session') as save:
            client.return_value.__enter__.return_value.discover_courses.side_effect = SchoolLoginRequired()
            self.assertEqual(service.ensure_session_locked(1)['cookie'], 'new')
            auth.assert_called_once_with('student', 'school-secret')
            save.assert_called_once_with(1, 'new', encrypted=True)
            with self.sessions() as db:
                self.assertEqual(unseal(1, db.get(User, 1).school_credentials)['cookie'], 'new')

    def test_failed_auth_stops_followup_attempts(self):
        with patch.object(service, 'ISpaceClient') as client, patch.object(service, 'authenticate', side_effect=SchoolAuthenticationError('failed')) as auth:
            client.return_value.__enter__.return_value.discover_courses.side_effect = SchoolLoginRequired()
            for _ in range(2):
                with self.assertRaises(SchoolLoginRequired):
                    service.ensure_session_locked(1)
            auth.assert_called_once()

    def test_deleted_account_cannot_be_repopulated(self):
        with self.sessions() as db:
            db.get(User, 1).school_credentials = None
            db.commit()
        with patch('app.services.school_sessions.save_school_session') as save:
            with self.assertRaises(SchoolLoginRequired):
                service.persist_credentials(1, self.data)
            save.assert_not_called()

    def test_cookie_cache_is_ciphertext(self):
        from app.services.school_sessions import save_school_session, session_path
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, ISPACE_SESSION_DIR=root):
            save_school_session(1, 'sensitive-cookie', encrypted=True)
            self.assertNotIn('sensitive-cookie', session_path(1).read_text())
