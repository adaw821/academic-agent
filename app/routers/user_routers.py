# 【前端配套新增】Cookie 读取浏览器中的会话令牌，hashlib 用来计算其哈希。
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, Request, BackgroundTasks
import hashlib
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.users import User
from app.schemas.users import UserCreate, UserLogin, EmailCodeRequest, PasswordReset
from pwdlib import PasswordHash
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, or_, func, delete
# get_currentUser 验证登录；UserSession 用于退出时删除会话。
from app.services.user_services import generate_token, get_currentUser
from app.models.user_session import UserSession

from app.services.email_auth import settings, rate_limit, deliver_code, verify_code, notify_reset

password_hasher = PasswordHash.recommended()

router = APIRouter()


# 【前端配套新增：当前用户接口】页面刷新后，前端调用它恢复用户显示。
@router.get("/user/me")
def current_user(user: User = Depends(get_currentUser)):
    # Depends 先检查 Cookie 和会话；有效时返回 User，无效时直接返回 401。
    # 只把 ID 和用户名发送给前端，不返回密码哈希或会话令牌。
    return {"id": user.id, "username": user.username}
# 【前端配套新增结束：当前用户接口】


# 【前端配套新增：退出登录接口】既注销服务端会话，也清除浏览器 Cookie。
@router.post("/user/logout")
def logout(
    response: Response,
    session_id: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    # 这里不依赖 get_currentUser：即使会话已过期，也允许清理 Cookie。
    if session_id:
        # Cookie 保存原始令牌，数据库主键保存哈希，所以先计算哈希再查询。
        token_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        session = db.get(UserSession, token_hash)
        if session is not None:
            # 删除当前这一次登录的会话；不影响该用户其他设备的会话。
            db.delete(session)
            db.commit()
    # 与登录时相同的 Cookie 名称和 path，让浏览器删除对应 Cookie。
    response.delete_cookie("session_id", path="/", httponly=True, samesite="lax")
    return {"message": "已退出登录"}
# 【前端配套新增结束：退出登录接口】

@router.post('/user/email-code', status_code=202)
def request_email_code(body: EmailCodeRequest, request: Request, background: BackgroundTasks,
                       db: Session = Depends(get_db)):
    settings()
    # Use the trusted socket peer; never trust arbitrary X-Forwarded-For headers.
    rate_limit(db, 'ip:' + (request.client.host if request.client else 'unknown'), 3600, 30)
    rate_limit(db, 'email:' + body.email, 60, 1)
    rate_limit(db, 'daily:' + body.email, 86400, 10)
    db.commit()
    background.add_task(deliver_code, body.email, body.purpose)
    return {'message': '若邮箱符合条件，验证码将发送至邮箱，请检查收件箱及垃圾邮件。', 'retry_after': 60}


@router.post('/user/reset-password')
def reset_password(body: PasswordReset, response: Response, background: BackgroundTasks,
                   db: Session = Depends(get_db)):
    verify_code(db, body.email, 'reset', body.code)
    users = db.scalars(select(User).where(func.lower(User.email) == body.email).with_for_update()).all()
    if len(users) != 1:
        raise HTTPException(400, '验证码无效或已过期，请重新获取')
    user = users[0]
    user.password_hash = password_hasher.hash(body.password)
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    response.delete_cookie('session_id', path='/', httponly=True, samesite='lax')
    background.add_task(notify_reset, body.email)
    return {'message': '密码已重置，请使用新密码登录'}


"""注册"""
@router.post("/user/create", status_code=201)
def user_create(user: UserCreate, db: Session  = Depends(get_db)):
    verify_code(db, user.email, 'register', user.code)
    if db.scalar(select(User.id).where(func.lower(User.email) == user.email)):
        raise HTTPException(409, '该邮箱已注册，请登录或找回密码')
    if user.phone and db.scalar(select(User.id).where(User.phone == user.phone)):
        raise HTTPException(409, '该手机号已被使用，请更换或留空')

    hashed_password = password_hasher.hash(user.password)

    new_user = User(
        username = user.username,
        email = user.email,
        phone = user.phone,
        password_hash = hashed_password
    )

    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="注册信息冲突，请检查邮箱或手机号",
        )
    db.refresh(new_user)

    return {
        "id": new_user.id,
        "username": new_user.username,
        "email": new_user.email,
        "phone": new_user.phone,
    }


"""登录"""
@router.post("/user/login")
def user_login(
    user: UserLogin,
    response: Response,
    db: Session = Depends(get_db),
):
    account = user.account
    password = user.password

    existing_user = db.scalar(
        select(User).where(
            or_(
                func.lower(User.email) == account.lower(),
                User.phone == account,
            )
        ).with_for_update()
    )
    if existing_user is None:
        raise HTTPException(
            status_code=401,
            detail="账号或密码错误",
        )

    password_correct = password_hasher.verify(
        password,
        existing_user.password_hash,
    )

    if not password_correct:
        raise HTTPException(
            status_code=401,
            detail="账号或密码错误"
        )

    #分发令牌给浏览器
    session_data = generate_token(existing_user.id, db)
    response.set_cookie(
        key="session_id",
        value=session_data["token"],
        httponly=True,
        max_age=1800,  # 半小时，与数据库一致
        samesite="lax",
        secure=False,  # 本地 HTTP；上线 HTTPS 后改为 True
        path="/",
    )

    return {
        "user_id": existing_user.id,
        "message": "登录成功"
    }
