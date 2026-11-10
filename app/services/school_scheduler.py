"""Opt-in, single-host periodic sync for one explicitly selected local user."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import os
import threading

from sqlalchemy import select

from app.db.database import SessionLocal
from app.models.courses import Course
from app.models.users import User
from app.services.ispace import ISpaceClient, SchoolError, SchoolLoginRequired, activity_url
from app.services.school_sessions import load_school_session, session_path
from app.services.school_sync import sync_course


class SyncAlreadyRunning(RuntimeError):
    pass


_owned_locks = threading.local()


def owns_sync_lock(user_id):
    return user_id in getattr(_owned_locks, 'users', set())


def validate_options(user_id, interval_seconds):
    if type(user_id) is not int or user_id <= 0:
        raise ValueError('user_id must be a positive local user ID')
    if type(interval_seconds) is not int or not 300 <= interval_seconds <= 604800:
        raise ValueError('sync interval must be between 5 minutes and 7 days')


@contextmanager
def user_sync_lock(user_id):
    """OS lock survives neither exit nor crash; never unlink the shared lock file."""
    validate_options(user_id, 300)
    path = session_path(user_id).with_suffix('.sync.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), 'r+b') as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise SyncAlreadyRunning('this user already has a local sync worker') from None
            raise
        try:
            _owned_locks.users = getattr(_owned_locks, 'users', set()) | {user_id}
            yield
        finally:
            _owned_locks.users.discard(user_id)
            handle.seek(0)
            release()


def sync_due_courses(user_id, interval_seconds, *, stop=None):
    """One batch. The CLI holds user_sync_lock across batches and waiting."""
    validate_options(user_id, interval_seconds)
    stop = stop if stop is not None else threading.Event()
    started = datetime.now(timezone.utc)
    report = dict(user_id=user_id, started_at=started.isoformat(), status='ok', courses_due=0, results=[])
    table = Course.__table__
    with SessionLocal() as db:
        if db.scalar(select(User.__table__.c.id).where(User.__table__.c.id == user_id)) is None:
            return dict(report, status='user_not_found')
        courses = db.execute(select(table.c.id, table.c.source_url, table.c.last_synced_at).where(
            table.c.user_id == user_id, table.c.source_url.is_not(None),
            (table.c.last_synced_at.is_(None)) | (table.c.last_synced_at <= started - timedelta(seconds=interval_seconds)),
        ).order_by(table.c.id)).mappings().all()
    report['courses_due'] = len(courses)
    if stop.is_set():
        return dict(report, status='stopped')
    if not courses:
        return report
    try:
        cookie = load_school_session(user_id)
    except SchoolLoginRequired:
        return dict(report, status='school_login_required')
    with ISpaceClient(cookie) as client:
        for course in courses:
            if stop.is_set():
                report['status'] = 'stopped'
                break
            result = dict(course_id=course['id'])
            try:
                url = activity_url(course['source_url'], '/course/view.php')
                if url != course['source_url']:
                    raise SchoolError('noncanonical course source')
            except (SchoolError, ValueError):
                report['results'].append(dict(result, status='invalid_source'))
                report['status'] = 'partial_failure'
                continue
            try:
                # No open database session/transaction during school access.
                snapshot = client.collect_course(url)
                if stop.is_set():
                    report['status'] = 'stopped'
                    break
                if snapshot.source_url != course['source_url']:
                    raise SchoolError('course source changed')
                with SessionLocal() as db:
                    db.execute(select(User.__table__.c.id).where(User.__table__.c.id == user_id).with_for_update())
                    current = db.execute(select(table.c.source_url, table.c.last_synced_at).where(
                        table.c.id == course['id'], table.c.user_id == user_id,
                    ).with_for_update()).mappings().one_or_none()
                    if current is None or current['source_url'] != course['source_url'] or current['last_synced_at'] != course['last_synced_at']:
                        result['status'] = 'skipped_changed'
                    else:
                        saved = sync_course(db, user_id, snapshot)
                        result.update(status='synced', assignments=saved['assignments'], resources=saved['resources'],
                                      warning_count=len(saved['warnings']))
            except SchoolLoginRequired:
                report['results'].append(dict(result, status='school_login_required'))
                report['status'] = 'school_login_required'
                break
            except Exception:
                # Preserve old data and continue; never log upstream text, URLs or SQL parameters.
                result['status'] = 'sync_failed'
                report['status'] = 'partial_failure'
            report['results'].append(result)
    return report


def run_schedule(user_id, interval_seconds, *, once=False, stop=None, emit, deadline_reminders=False):
    validate_options(user_id, interval_seconds)
    stop = stop if stop is not None else threading.Event()
    with user_sync_lock(user_id):
        while not stop.is_set():
            try:
                report = sync_due_courses(user_id, interval_seconds, stop=stop)
            except Exception:
                report = dict(user_id=user_id, status='batch_failed')
            if deadline_reminders:
                report['reminders'] = {'status': 'skipped'}
                if report['status'] in {'ok', 'partial_failure'} and not stop.is_set():
                    try:
                        from app.services.deadline_reminders import generate_deadline_reminders
                        with SessionLocal() as db:
                            created = generate_deadline_reminders(db, user_id)
                            db.commit()
                        report['reminders'] = {'status': 'ok', 'created': created}
                    except Exception:
                        report['reminders'] = {'status': 'failed'}
            emit(report)
            if once or report['status'] == 'user_not_found':
                return 0 if report['status'] in {'ok', 'stopped'} and report.get('reminders', {}).get('status') != 'failed' else 1
            # Fixed delay after completion; no overlapping runs or catch-up bursts.
            if stop.wait(interval_seconds):
                break
    return 0
