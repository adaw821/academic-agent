import unittest
from test_email_auth import Base, User, create_engine, sessionmaker
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.services.school_onboarding import import_mis
from sqlalchemy import select, func


class MISImportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine, tables=[User.__table__, Course.__table__, CourseSchedule.__table__])
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(User(id=1, username='test', email='test@example.test', password_hash='unused'))
        self.db.commit()
        self.snapshot = {'term': '2026 Semester 1', 'courses': [dict(course_code='TEST1', course_name='Example', teachers='Teacher', times=['Mon 8:00-9:50', 'Thu 13:00-13:50'], rooms=['A101', 'B201'])]}

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_repeated_import_is_idempotent_and_preserves_term(self):
        self.assertEqual(import_mis(self.db, 1, self.snapshot), 2)
        self.assertEqual(import_mis(self.db, 1, self.snapshot), 0)
        rows = self.db.scalars(select(CourseSchedule)).all()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].start_time, '08:00')
        self.assertEqual(rows[0].term_name, '2026 Semester 1')
        self.assertIsNone(rows[0].term_start)

    def test_unknown_time_never_partially_imports(self):
        self.snapshot['courses'][0]['times'][1] = 'TBA'
        with self.assertRaises(ValueError):
            import_mis(self.db, 1, self.snapshot)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Course)), 0)

    def test_unscheduled_course_does_not_get_fake_schedule(self):
        self.snapshot['courses'][0].update(times=[], rooms=[])
        self.assertEqual(import_mis(self.db, 1, self.snapshot), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Course)), 1)
