"""Timetable regression checks; run directly with the project's Python environment.

Uses a temporary SQLite database and mocks only the vision model response.
No external API requests or writes to the development database are made.
"""

import json
import os
import hashlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEMP = tempfile.TemporaryDirectory()

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from openai import APITimeoutError
from PIL import Image
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

with patch.dict(os.environ, {
    "DATABASE_URL": "sqlite:///" + (Path(TEMP.name) / "timetable.db").as_posix(),
    "ZHIPU_API_KEY": "unused-test-key",
}):
    from app.db.database import Base, get_db
    from app.routers.timetable_upload import router
    from app.services import timetable_service as service

# Register the related ORM models without importing unrelated API services.
from app.models.users import User
from app.models.projects import Project
from app.models.documents import Document
from app.models.document_parents import Parent
from app.models.document_child import Child
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.models.user_session import UserSession
from app.services.user_services import generate_token, get_currentUser

engine = create_engine("sqlite:///" + (Path(TEMP.name) / "timetable.db").as_posix())
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def override_db():
    with SessionLocal() as db:
        yield db


@event.listens_for(engine, "connect")
def enable_foreign_keys(connection, _):
    connection.execute("PRAGMA foreign_keys=ON")


@event.listens_for(UserSession, "load")
def restore_sqlite_session_timezone(session, context):
    # SQLite drops tzinfo; PostgreSQL's production TIMESTAMPTZ retains it.
    # Restore that storage behavior so tests can use the real cookie validator.
    if context.session.get_bind().dialect.name == "sqlite" and session.expires_at.tzinfo is None:
        session.expires_at = session.expires_at.replace(tzinfo=timezone.utc)


TABLES = [User.__table__, Course.__table__, CourseSchedule.__table__, UserSession.__table__]
UNSET = object()


def image_bytes(mime):
    buffer = BytesIO()
    with Image.new("RGB", (32, 32), "white") as image:
        image.save(buffer, format="JPEG" if mime == "image/jpeg" else "PNG")
    return buffer.getvalue()


TIMETABLE = {
    "course_name": "Machine Learning",
    "course_code": "COMP2073",
    "location": "t8-209",
    "day_of_week": "Mon",
    "start_time": "19:00",
    "end_time": "21:00",
    "teachers": "Ms.Angela",
    "units": 3.0,
}


class TimetableUploadTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine, tables=TABLES)
        Base.metadata.create_all(engine, tables=TABLES)
        with SessionLocal() as db:
            db.add_all([
                User(id=user_id, username=f"user{user_id}",
                     email=f"user{user_id}@example.com", password_hash="unused")
                for user_id in (1, 2)
            ])
            db.commit()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.dependency_overrides[get_db] = override_db
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.vision = AsyncMock()
        vision_patch = patch.object(service, "llm", SimpleNamespace(ainvoke=self.vision))
        vision_patch.start()
        self.addCleanup(vision_patch.stop)
        env_patch = patch.dict(os.environ, {"ZHIPU_API_KEY": "unused-test-key"})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        # Exercise the real OCR request/validation path without contacting the provider.
        self.ocr = AsyncMock()
        self.ocr.post.return_value = httpx.Response(
            200, json={"md_results": "OCR course text"},
            request=httpx.Request("POST", service.LAYOUT_PARSING_URL),
        )
        ocr_patch = patch.object(service.httpx, "AsyncClient")
        ocr_patch.start().return_value.__aenter__.return_value = self.ocr
        self.addCleanup(ocr_patch.stop)

    def upload(self, user_id=1, rows=None, mime="image/png", content=None,
               model_content=UNSET, form_data=None):
        if user_id is None:
            self.app.dependency_overrides.pop(get_currentUser, None)
        else:
            self.app.dependency_overrides[get_currentUser] = (
                lambda: SimpleNamespace(id=user_id)
            )
        self.vision.return_value = SimpleNamespace(
            content=(json.dumps(rows if rows is not None else [TIMETABLE])
                     if model_content is UNSET else model_content)
        )
        return self.client.post(
            "/api/timetable",
            data=form_data,
            files={"file": ("timetable.png" if mime == "image/png" else "timetable.jpg",
                            image_bytes(mime) if content is None else content, mime)},
        )

    def assert_no_timetable_rows(self):
        with SessionLocal() as db:
            self.assertEqual(list(db.scalars(select(Course))), [])
            self.assertEqual(list(db.scalars(select(CourseSchedule))), [])

    def test_corrupt_images_are_rejected_before_model_call(self):
        for mime in ("image/png", "image/jpeg"):
            for content in (b"random bytes\x00\xff", image_bytes(mime)[:-30]):
                with self.subTest(mime=mime, size=len(content)):
                    response = self.upload(mime=mime, content=content)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIn("图片", response.json()["detail"])
                    self.vision.assert_not_awaited()
                    self.assert_no_timetable_rows()

    def test_mismatched_image_type_is_rejected(self):
        response = self.upload(mime="image/png", content=image_bytes("image/jpeg"))
        self.assertEqual(response.status_code, 400, response.text)
        self.vision.assert_not_awaited()
        self.assert_no_timetable_rows()

    def test_no_timetable_or_unreadable_model_output_returns_friendly_error(self):
        for model_content in ("[]", "图片中没有课表", "", None, [],
                              "[invalid JSON]", '[null, "unreadable"]',
                              '[{"course_name": "unreadable"}]'):
            with self.subTest(model_content=model_content):
                response = self.upload(model_content=model_content)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("清晰图片", response.json()["detail"])
                self.assert_no_timetable_rows()

    def test_blurry_image_with_unreadable_required_fields_is_not_persisted(self):
        for field in service.NECESSARY_ATTRIBUTES:
            with self.subTest(field=field):
                response = self.upload(rows=[dict(TIMETABLE, **{field: None})])
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("清晰图片", response.json()["detail"])
                self.assert_no_timetable_rows()

    def test_provider_failure_is_reported_as_service_error(self):
        self.vision.side_effect = APITimeoutError(
            request=httpx.Request("POST", "https://example.com/vision"),
        )
        with self.assertLogs(service.logger, level="ERROR"):
            response = self.upload()
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"], "课表识别服务暂时不可用，请稍后重试")
        self.assert_no_timetable_rows()

    def test_unexpected_errors_are_not_mislabeled_as_invalid_images(self):
        self.vision.side_effect = RuntimeError("unexpected internal error")
        response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assert_no_timetable_rows()

    def test_empty_file_and_unsupported_mime_keep_existing_errors(self):
        for content, mime in ((b"", "image/png"), (b"text", "text/plain")):
            with self.subTest(mime=mime):
                response = self.upload(content=content, mime=mime)
                self.assertEqual(response.status_code, 400, response.text)
                self.vision.assert_not_awaited()
                self.assert_no_timetable_rows()

    def test_valid_json_in_code_fence_still_succeeds(self):
        response = self.upload(model_content="```json\n" + json.dumps([TIMETABLE]) + "\n```")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["timetables"], [TIMETABLE])

    def test_png_and_jpeg_persist_course_and_schedule_for_current_user(self):
        for user_id, mime in ((1, "image/png"), (2, "image/jpeg")):
            with self.subTest(mime=mime):
                response = self.upload(user_id=user_id, mime=mime)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["timetables"], [TIMETABLE])
                with SessionLocal() as db:
                    course = db.scalar(select(Course).where(Course.user_id == user_id))
                    self.assertIsNotNone(course)
                    self.assertEqual(course.course_code, "COMP2073")
                    self.assertEqual(course.teachers, "Ms.Angela")
                    self.assertEqual(course.units, 3.0)
                    schedule = db.scalar(select(CourseSchedule).where(
                        CourseSchedule.course_id == course.id,
                    ))
                    self.assertIsNotNone(schedule)
                    self.assertEqual(schedule.start_time, "19:00")

    def test_anonymous_upload_is_rejected_before_model_call(self):
        response = self.upload(user_id=None)
        self.assertEqual(response.status_code, 401, response.text)
        self.vision.assert_not_awaited()
        with SessionLocal() as db:
            self.assertEqual(list(db.scalars(select(Course))), [])

    def test_invalid_and_expired_cookies_are_rejected_before_model_call(self):
        expired_token = "expired-test-session"
        with SessionLocal() as db:
            db.add(UserSession(
                token_hash=hashlib.sha256(expired_token.encode()).hexdigest(),
                user_id=1,
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ))
            db.commit()
        for token, detail in (("unknown-session", "不存在的会话"),
                              (expired_token, "登录已过期，请重新登录")):
            with self.subTest(token=token):
                self.client.cookies.set("session_id", token)
                response = self.upload(user_id=None)
                self.assertEqual(response.status_code, 401, response.text)
                self.assertEqual(response.json()["detail"], detail)
                self.vision.assert_not_awaited()
                self.assert_no_timetable_rows()

    def test_cookie_users_keep_same_code_different_schedules_separate(self):
        with SessionLocal() as db:
            tokens = {user_id: generate_token(user_id, db)["token"] for user_id in (1, 2)}
        rows = {
            1: dict(TIMETABLE),
            2: dict(TIMETABLE, day_of_week="Wed", start_time="09:00",
                    end_time="11:00", location="t9-101", teachers="Mr.Bob"),
        }
        # Real Cookie -> session lookup -> User -> course ownership, then repeat
        # both uploads to verify duplicate checks remain within each user's course.
        for _ in range(2):
            for user_id in (1, 2):
                self.client.cookies.set("session_id", tokens[user_id])
                response = self.upload(user_id=None, rows=[rows[user_id]])
                self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(get_currentUser, self.app.dependency_overrides)
        with SessionLocal() as db:
            courses = list(db.scalars(select(Course).order_by(Course.user_id)))
            self.assertEqual([course.user_id for course in courses], [1, 2])
            self.assertNotEqual(courses[0].id, courses[1].id)
            self.assertEqual(len(list(db.scalars(select(CourseSchedule)))), 2)
            for course in courses:
                expected = rows[course.user_id]
                self.assertEqual(course.course_code, expected["course_code"])
                self.assertEqual(course.teachers, expected["teachers"])
                schedules = list(db.scalars(select(CourseSchedule).where(
                    CourseSchedule.course_id == course.id,
                )))
                self.assertEqual(len(schedules), 1)
                for field in ("day_of_week", "start_time", "end_time", "location"):
                    self.assertEqual(getattr(schedules[0], field), expected[field])

    def test_form_user_id_cannot_override_cookie_owner(self):
        with SessionLocal() as db:
            token = generate_token(1, db)["token"]
        self.client.cookies.set("session_id", token)
        response = self.upload(user_id=None, form_data={"user_id": "2"})
        self.assertEqual(response.status_code, 200, response.text)
        with SessionLocal() as db:
            courses = list(db.scalars(select(Course)))
            self.assertEqual([course.user_id for course in courses], [1])

    def test_reupload_does_not_duplicate_course_or_schedule(self):
        for _ in range(2):
            self.assertEqual(self.upload().status_code, 200)
        with SessionLocal() as db:
            self.assertEqual(len(list(db.scalars(select(Course)))), 1)
            self.assertEqual(len(list(db.scalars(select(CourseSchedule)))), 1)

    def test_course_code_lookup_does_not_modify_another_users_course(self):
        incomplete = dict(TIMETABLE, teachers=None, units=None)
        self.assertEqual(self.upload(user_id=1, rows=[incomplete]).status_code, 200)
        self.assertEqual(self.upload(user_id=2).status_code, 200)
        with SessionLocal() as db:
            courses = list(db.scalars(select(Course).order_by(Course.user_id)))
            self.assertEqual([c.user_id for c in courses], [1, 2])
            self.assertIsNone(courses[0].teachers)
            self.assertIsNone(courses[0].units)
            self.assertEqual(courses[1].teachers, "Ms.Angela")

    def test_name_fallback_is_scoped_to_user_with_and_without_course_code(self):
        incomplete = dict(TIMETABLE, course_code=None, teachers=None, units=None)
        self.assertEqual(self.upload(user_id=1, rows=[incomplete]).status_code, 200)
        self.assertEqual(self.upload(user_id=2, rows=[incomplete]).status_code, 200)
        # User 2 can enrich their own course using the name fallback.
        self.assertEqual(self.upload(user_id=2).status_code, 200)
        with SessionLocal() as db:
            courses = list(db.scalars(select(Course).order_by(Course.user_id)))
            self.assertEqual([c.user_id for c in courses], [1, 2])
            self.assertIsNone(courses[0].course_code)
            self.assertIsNone(courses[0].teachers)
            self.assertEqual(courses[1].course_code, "COMP2073")
            self.assertEqual(courses[1].teachers, "Ms.Angela")
            self.assertEqual(len(list(db.scalars(select(CourseSchedule)))), 2)

    def test_database_failure_rolls_back_entire_upload(self):
        # SQLite cannot bind a list as a String; the first row must be rolled back too.
        invalid = dict(TIMETABLE, course_name="Data Structures", course_code="COMP2000",
                       teachers=["invalid teacher value"])
        response = self.upload(rows=[TIMETABLE, invalid])
        self.assertEqual(response.status_code, 500)
        with SessionLocal() as db:
            self.assertEqual(list(db.scalars(select(Course))), [])
            self.assertEqual(list(db.scalars(select(CourseSchedule))), [])


def tearDownModule():
    engine.dispose()
    TEMP.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
