"""Real key loading, including .env updates after a server has started."""
import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services import school_crypto as crypto


class SchoolKeyConfigTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.key = self.root / 'school.key'
        self.key.write_bytes(base64.b64encode(b'k' * 32))
        self.env_file = self.root / '.env'
        self.env_file.write_text(f'SCHOOL_KEY_FILE={self.key.as_posix()}\n', encoding='utf-8')
        for patcher in (patch.object(crypto, 'ENV_FILE', self.env_file), patch.dict(os.environ, {}, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_process_setting_uses_local_file(self):
        value = crypto.seal(1, {'password': 'dummy-secret'})
        self.assertEqual(crypto.unseal(1, value), {'password': 'dummy-secret'})
        self.assertNotIn('SCHOOL_KEY_FILE', os.environ)

    def test_empty_inherited_setting_uses_local_file(self):
        for value in ('', '   '):
            os.environ['SCHOOL_KEY_FILE'] = value
            self.assertEqual(crypto.key_path(), self.key.resolve())
            crypto.cipher()

    def test_configuration_added_after_startup_is_seen(self):
        self.env_file.write_text('', encoding='utf-8')
        with self.assertRaises(ValueError):
            crypto.cipher()
        self.env_file.write_text(f'SCHOOL_KEY_FILE={self.key.as_posix()}\n', encoding='utf-8')
        crypto.cipher()

    def test_nonempty_process_setting_wins(self):
        other_key = self.root / 'deployment.key'
        other_key.write_bytes(base64.b64encode(b'd' * 32))
        os.environ['SCHOOL_KEY_FILE'] = str(other_key)
        self.assertEqual(crypto.key_path(), other_key.resolve())
        crypto.cipher()

    def test_bad_explicit_path_never_falls_back_or_creates_key(self):
        missing = self.root / 'missing.key'
        os.environ['SCHOOL_KEY_FILE'] = str(missing)
        with self.assertRaises(ValueError):
            crypto.cipher()
        self.assertFalse(missing.exists())

    def test_invalid_key_is_not_replaced_or_disclosed(self):
        self.key.write_bytes(b'not-a-valid-secret-key')
        with self.assertRaises(ValueError) as error:
            crypto.cipher()
        self.assertNotIn('not-a-valid-secret-key', str(error.exception))
        self.assertEqual(self.key.read_bytes(), b'not-a-valid-secret-key')

    def test_repository_key_remains_rejected(self):
        os.environ['SCHOOL_KEY_FILE'] = str(Path(crypto.__file__).resolve().parents[2] / 'unsafe.key')
        with self.assertRaises(ValueError):
            crypto.cipher()


if __name__ == '__main__':
    unittest.main()
