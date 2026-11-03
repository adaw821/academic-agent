"""Manual edits and image supplements use the same user-owned schedules."""
from types import SimpleNamespace
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest
from sqlalchemy import select

import test_timetable_upload as fixture
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.services.user_services import get_currentUser


class TimetableEntriesTests(unittest.TestCase):
    setUp = fixture.TimetableUploadTests.setUp
    upload = fixture.TimetableUploadTests.upload

    def login(self, user_id=1):
        self.app.dependency_overrides[get_currentUser] = lambda: SimpleNamespace(id=user_id)

    def create(self, **overrides):
        body = {key: value for key, value in fixture.TIMETABLE.items() if key != 'units'}
        return self.client.post('/api/timetable/entries', json={**body, **overrides})

    def test_create_validate_duplicates_and_edit_without_losing_calendar(self):
        self.login()
        created = self.create()
        self.assertEqual(created.status_code, 201, created.text)
        ident = created.json()['id']
        self.assertEqual(self.create().status_code, 409)
        for changes in ({'day_of_week': 'Monday'}, {'start_time': '25:00'},
                        {'end_time': '18:00'}, {'course_name': '  '}, {'user_id': 2}):
            self.assertEqual(self.create(**changes).status_code, 422)
        with fixture.SessionLocal() as db:
            db.get(CourseSchedule, ident).term_name = '秋季'
            db.commit()
        body = dict(day_of_week='Tue', start_time='10:00', end_time='11:00', location='B201')
        self.assertEqual(self.client.put(f'/api/timetable/entries/{ident}', json=body).status_code, 200)
        with fixture.SessionLocal() as db:
            row = db.get(CourseSchedule, ident)
            self.assertEqual((row.day_of_week, row.location, row.term_name), ('Tue', 'B201', '秋季'))

    def test_other_user_cannot_edit_delete_and_anonymous_cannot_create(self):
        self.login()
        ident = self.create().json()['id']
        self.login(2)
        body = dict(day_of_week='Tue', start_time='10:00', end_time='11:00')
        self.assertEqual(self.client.put(f'/api/timetable/entries/{ident}', json=body).status_code, 404)
        self.assertEqual(self.client.delete(f'/api/timetable/entries/{ident}').status_code, 404)
        self.assertEqual(self.create().status_code, 201)
        self.app.dependency_overrides.pop(get_currentUser)
        self.assertEqual(self.create().status_code, 401)

    def test_image_supplement_keeps_manual_slot_and_delete_keeps_course(self):
        self.login()
        ident = self.create(day_of_week='Tue').json()['id']
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(self.upload().status_code, 200)
        with fixture.SessionLocal() as db:
            self.assertEqual(len(list(db.scalars(select(Course)))), 1)
            self.assertEqual(len(list(db.scalars(select(CourseSchedule)))), 2)
        self.assertEqual(self.client.delete(f'/api/timetable/entries/{ident}').status_code, 200)
        with fixture.SessionLocal() as db:
            self.assertEqual(len(list(db.scalars(select(Course)))), 1)
            self.assertEqual(len(list(db.scalars(select(CourseSchedule)))), 1)
        self.assertEqual(self.client.delete(f'/api/timetable/entries/{ident}').status_code, 404)

    def test_agent_image_upload_is_immutable_and_account_scoped(self):
        from app.services import timetable_attachments as attachments
        from fastapi import HTTPException
        self.login()
        with tempfile.TemporaryDirectory() as folder, patch.object(attachments, 'ROOT', Path(folder)):
            result = self.client.post('/api/timetable/attachments', files={
                'file': ('课表.png', fixture.image_bytes('image/png'), 'image/png')})
            self.assertEqual(result.status_code, 201, result.text)
            ident = result.json()['image_id']
            self.assertTrue(attachments.read_image(1, ident).startswith(b'\x89PNG'))
            with self.assertRaises(HTTPException) as raised:
                attachments.read_image(2, ident)
            self.assertEqual(raised.exception.status_code, 404)
            with self.assertRaises(HTTPException):
                attachments.read_image(1, '../outside')
            invalid = self.client.post('/api/timetable/attachments', files={'file': ('fake.png', b'not image', 'image/png')})
            self.assertEqual(invalid.status_code, 400)


if __name__ == '__main__':
    unittest.main()
