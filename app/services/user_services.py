import secrets
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from app.models.user_session import UserSession
from fastapi import Cookie, Depends, HTTPException
from app.db.database import get_db
from app.models.users import User

def generate_token(user_id: int, db: Session):
    # 原始令牌给浏览器，哈希存数据库
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    expired_time = datetime.now(timezone.utc) + timedelta(minutes=1)

    new_session = UserSession(
        token_hash=token_hash,
        user_id=user_id,
        expires_at=expired_time,
    )

    db.add(new_session)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "token": token,
        "expires_at": expired_time,
    }

def get_currentUser(
    session_id: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not session_id:
        raise HTTPException(status_code=401, detail="请先登录")

    session_id_hashed = hashlib.sha256(
        session_id.encode("utf-8")
    ).hexdigest()

    session = db.get(UserSession, session_id_hashed)
    if not session:
        raise HTTPException(status_code=401, detail="不存在的会话")


    #维护登录状态
    expired_time = session.expires_at
    if expired_time <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")

    user_id = session.user_id
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user
