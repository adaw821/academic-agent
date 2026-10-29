"""Verify additive migration and rollback preserve existing rows."""
import importlib.util
from pathlib import Path
import unittest
from sqlalchemy import create_engine, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations

path = Path(__file__).resolve().parents[1] / 'migration/versions/d91e73b08422_backend_completion.py'
spec = importlib.util.spec_from_file_location('backend_migration', path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class BackendSchemaTests(unittest.TestCase):
    def test_upgrade_and_downgrade_preserve_original_data(self):
        engine = create_engine('sqlite://')
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE projects (id INTEGER PRIMARY KEY)')
            conn.exec_driver_sql('CREATE TABLE documents (id INTEGER PRIMARY KEY, filename TEXT NOT NULL)')
            conn.exec_driver_sql('CREATE TABLE message (id INTEGER PRIMARY KEY, content TEXT NOT NULL)')
            conn.exec_driver_sql("INSERT INTO documents VALUES (1, 'original.txt')")
            conn.exec_driver_sql("INSERT INTO message VALUES (1, 'original answer')")
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            self.assertIn('workflow_runs', inspect(conn).get_table_names())
            self.assertIsNone(conn.exec_driver_sql('SELECT parsed_text FROM documents').scalar())
            self.assertIsNone(conn.exec_driver_sql('SELECT response_data FROM message').scalar())
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            self.assertNotIn('workflow_runs', inspect(conn).get_table_names())
            self.assertEqual(conn.exec_driver_sql('SELECT filename FROM documents').scalar(), 'original.txt')
            self.assertEqual(conn.exec_driver_sql('SELECT content FROM message').scalar(), 'original answer')
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
