"""Exercise the actual migration on an isolated SQLite database."""
import importlib.util
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite://")
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models.assignments import Assignment
from app.models.course_resources import CourseResource
from app.models.courses import Course

path = Path(__file__).resolve().parents[1] / "migration/versions/f72a913c604b_add_course_assignments_resources.py"
spec = importlib.util.spec_from_file_location("school_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class SchoolSchemaTests(unittest.TestCase):
    def test_migration_constraints_and_existing_data(self):
        engine = create_engine("sqlite://")
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            conn.exec_driver_sql("INSERT INTO users VALUES (1), (2)")
            conn.exec_driver_sql("CREATE TABLE courses (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), course_name VARCHAR(50) NOT NULL, course_code VARCHAR(20), teachers VARCHAR(100), units FLOAT)")
            conn.exec_driver_sql("INSERT INTO courses (id,user_id,course_name) VALUES (1,1,'Database'),(2,2,'Database')")
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            self.assertEqual(conn.scalar(text("SELECT count(*) FROM courses")), 2)
            for model in (Course, Assignment, CourseResource):
                self.assertEqual(set(model.__table__.columns.keys()), {c['name'] for c in inspect(conn).get_columns(model.__tablename__)})
            conn.exec_driver_sql("UPDATE courses SET source_url='https://school/course/1'")
            # Same school activity is allowed for different users' courses.
            for course_id in (1, 2):
                conn.execute(Assignment.__table__.insert().values(id=course_id, course_id=course_id, external_activity_id='387779', title='lab5', source_url='https://school/assignment/1', last_synced_at=datetime.now(timezone.utc)))
            self.assertEqual(conn.scalar(text("SELECT count(*) FROM assignments WHERE due_at IS NULL")), 2)
            with self.assertRaises(IntegrityError):
                conn.exec_driver_sql("INSERT INTO assignments (course_id,external_activity_id,title,source_url,last_synced_at) VALUES (1,'387779','duplicate','url',CURRENT_TIMESTAMP)")
            conn.exec_driver_sql("INSERT INTO course_resources (id,course_id,assignment_id,source_key,title,resource_type,source_url,last_synced_at) VALUES (1,1,1,'file:1','Questions','file','url',CURRENT_TIMESTAMP)")
            with self.assertRaises(IntegrityError):
                conn.exec_driver_sql("INSERT INTO course_resources (course_id,assignment_id,source_key,title,resource_type,source_url,last_synced_at) VALUES (2,1,'file:2','Wrong course','file','url',CURRENT_TIMESTAMP)")
            with self.assertRaises(IntegrityError):
                conn.exec_driver_sql("INSERT INTO course_resources (course_id,source_key,title,resource_type,source_url,last_synced_at) VALUES (1,'file:1','duplicate','file','url',CURRENT_TIMESTAMP)")
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            self.assertNotIn('assignments', inspect(conn).get_table_names())
            self.assertEqual(conn.scalar(text("SELECT count(*) FROM courses")), 2)
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
