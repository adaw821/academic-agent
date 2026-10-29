"""Download a single owned school resource without changing its sync metadata."""
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from app.models.courses import Course
from app.models.course_resources import CourseResource
from app.services.ispace import ISpaceClient, SchoolError, SchoolLoginRequired
from app.services.school_sessions import load_school_session

DOWNLOAD_ROOT = Path('data/uploads/school')


def owned_resource(db, user_id, course_id, resource_id, *, lock=False):
    table = CourseResource.__table__
    query = select(table).join(Course, Course.id == table.c.course_id).where(
        Course.user_id == user_id, Course.id == course_id, table.c.id == resource_id)
    if lock:
        query = query.with_for_update()
    row = db.execute(query).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, '课程资源不存在')
    return row


def resource_directory(user_id, course_id, resource_id):
    root = DOWNLOAD_ROOT.resolve()
    expected = root / str(user_id) / str(course_id) / str(resource_id)
    if expected.resolve() != expected:
        raise HTTPException(409, '附件存储目录无效')
    return expected


def saved_file(row, directory):
    if not row['storage_path']:
        return None
    path = Path(row['storage_path']).resolve()
    if path.parent != directory:
        raise HTTPException(409, '附件存储路径无效')
    return path if path.is_file() else None


def download_status(db, user_id, course_id, resource_id):
    row = owned_resource(db, user_id, course_id, resource_id)
    path = saved_file(row, resource_directory(user_id, course_id, resource_id))
    return dict(resource_id=resource_id, downloaded=path is not None,
                size_bytes=path.stat().st_size if path else None,
                content_type=row['content_type'] if path else None)


def download_resource(db, user_id, course_id, resource_id):
    row = owned_resource(db, user_id, course_id, resource_id)
    if row['resource_type'] not in {'file', 'resource'} or row['purpose'] == 'student_submission':
        raise HTTPException(422, '该资源不是可下载的教学附件')
    directory = resource_directory(user_id, course_id, resource_id)
    existing = saved_file(row, directory)
    if existing:
        return dict(resource_id=resource_id, downloaded=True, cached=True,
                    size_bytes=existing.stat().st_size, content_type=row['content_type'])
    source_url = row['source_url']
    db.rollback()  # Do not hold a database transaction while requesting school files.
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / (uuid4().hex + '.part')
    final = None
    committed = False
    try:
        with ISpaceClient(load_school_session(user_id)) as client:
            metadata = client.download_resource(source_url, temporary)
        row = owned_resource(db, user_id, course_id, resource_id, lock=True)
        if row['source_url'] != source_url:
            raise HTTPException(409, '资源来源已变化，请重新下载')
        existing = saved_file(row, directory)
        if existing:  # Another request completed this resource while we downloaded.
            return dict(resource_id=resource_id, downloaded=True, cached=True,
                        size_bytes=existing.stat().st_size, content_type=row['content_type'])
        final = temporary.with_suffix(metadata['suffix'])
        temporary.replace(final)
        db.execute(update(CourseResource.__table__).where(CourseResource.id == resource_id).values(
            storage_path=str(final), content_type=metadata['content_type']))
        db.commit()
        committed = True
        return dict(resource_id=resource_id, downloaded=True, cached=False,
                    size_bytes=metadata['size_bytes'], content_type=metadata['content_type'])
    except SchoolLoginRequired:
        raise HTTPException(409, detail={'code': 'school_login_required', 'message': '学校会话缺失、已过期或无权访问附件'}) from None
    except SchoolError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        if not committed:
            db.rollback()
            if final is not None:
                final.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
