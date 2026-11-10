"""Authenticated account maintenance; no credential read API."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.users import User
from app.schemas.users import SchoolCredentials
from app.services.user_services import get_currentUser
from app.services.school_crypto import seal
from app.services.school_auth import authenticate, SchoolAuthenticationError, SchoolConnectionError
from app.services.school_onboarding import sync_school
from app.services.school_scheduler import user_sync_lock, SyncAlreadyRunning
from app.services.school_sessions import remove_school_session
from app.services.email_auth import rate_limit

router = APIRouter(tags=['school account'])


@router.get('/school/account')
def status(user: User = Depends(get_currentUser)):
    return {'connected': bool(user.school_credentials), 'state': user.school_sync_state,
            'message': user.school_sync_message}


@router.put('/school/account')
def update(body: SchoolCredentials, background: BackgroundTasks, db: Session = Depends(get_db),
           user: User = Depends(get_currentUser)):
    user_id = user.id
    rate_limit(db, f'school-update:{user_id}', 600, 5)
    rate_limit(db, 'school-account:' + body.school_account.casefold(), 600, 5)
    db.commit()
    try:
        with user_sync_lock(user_id):
            data = authenticate(body.school_account, body.school_password.get_secret_value())
            user = db.get(User, user_id)
            user.school_credentials = seal(user_id, data)
            user.school_sync_state, user.school_sync_message = 'queued', '学校连接已更新，等待同步。'
            db.commit()
            remove_school_session(user_id)
    except SyncAlreadyRunning:
        raise HTTPException(409, '学校同步正在进行，请完成后重试') from None
    except SchoolConnectionError as exc:
        raise HTTPException(503, str(exc)) from None
    except (SchoolAuthenticationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None
    background.add_task(sync_school, user_id)
    return {'message': '学校连接已更新，正在同步。'}


@router.delete('/school/account')
def disconnect(db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    user_id = user.id
    try:
        with user_sync_lock(user_id):
            user.school_credentials = None
            user.school_sync_state, user.school_sync_message = None, None
            db.commit()
            remove_school_session(user_id)
    except SyncAlreadyRunning:
        raise HTTPException(409, '学校同步正在进行，请完成后重试删除连接') from None
    return {'message': '已删除学校凭据和会话，已导入课程保留。'}


@router.post('/school/account/sync', status_code=202)
def retry(background: BackgroundTasks, user: User = Depends(get_currentUser)):
    if not user.school_credentials or user.school_sync_state == 'authentication_required':
        raise HTTPException(409, '请先更新学校账密')
    background.add_task(sync_school, user.id)
    return {'message': '已请求同步；若已有同步正在执行，将继续现有任务。'}
