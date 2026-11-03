"""Single-use email challenges, transactional verification and SMTP delivery."""
import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.db.database import SessionLocal
from app.models.email_code import EmailCode, EmailRate
from app.models.users import User


def settings():
    values = {name: os.environ.get(name, '') for name in (
        'SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_FROM', 'EMAIL_CODE_SECRET')}
    if not all(values.values()) or len(values['EMAIL_CODE_SECRET']) < 32:
        raise HTTPException(503, '邮件服务尚未配置，请联系管理员')
    mode = os.environ.get('SMTP_SECURITY', 'ssl')
    if mode not in {'ssl', 'starttls'}:
        raise HTTPException(503, '邮件服务配置无效')
    try:
        port = int(os.environ.get('SMTP_PORT', '465' if mode == 'ssl' else '587'))
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise HTTPException(503, '邮件服务配置无效') from None
    return values, mode, port


def digest(email, purpose, code):
    key = settings()[0]['EMAIL_CODE_SECRET'].encode()
    return hmac.new(key, f'{email}\0{purpose}\0{code}'.encode(), hashlib.sha256).hexdigest()


def insert(db, model):
    return (pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert)(model)


def rate_limit(db, key, seconds, limit):
    now = int(time.time())
    key = hashlib.sha256(key.encode()).hexdigest()
    db.execute(insert(db, EmailRate).values(key=key, until=now + seconds, count=0).on_conflict_do_nothing())
    row = db.scalar(select(EmailRate).where(EmailRate.key == key).with_for_update())
    if row.until <= now:
        row.until, row.count = now + seconds, 0
    if row.count >= limit:
        raise HTTPException(429, '操作过于频繁，请稍后再试')
    row.count += 1
    db.flush()


def send_mail(email, subject, body):
    values, mode, port = settings()
    message = EmailMessage()
    message['From'], message['To'], message['Subject'] = values['SMTP_FROM'], email, subject
    message.set_content(body)
    context = ssl.create_default_context()
    smtp = smtplib.SMTP_SSL(values['SMTP_HOST'], port, timeout=15, context=context) if mode == 'ssl' else smtplib.SMTP(values['SMTP_HOST'], port, timeout=15)
    with smtp:
        if mode == 'starttls':
            smtp.starttls(context=context)
        smtp.login(values['SMTP_USER'], values['SMTP_PASSWORD'])
        smtp.send_message(message)


def deliver_code(email, purpose):
    # Same asynchronous response for existing/missing accounts. No credentials in logs.
    try:
        with SessionLocal() as db:
            users = db.scalars(select(User).where(func.lower(User.email) == email)).all()
            if (purpose == 'register' and users) or (purpose == 'reset' and len(users) != 1):
                return
            code = f'{secrets.randbelow(1000000):06d}'
            values = dict(email=email, purpose=purpose, digest=digest(email, purpose, code), expires=int(time.time()) + 600, attempts=0)
            stmt = insert(db, EmailCode).values(**values)
            db.execute(stmt.on_conflict_do_update(index_elements=['email', 'purpose'], set_=values))
            send_mail(email, '学术助手：邮箱验证码', f'你的{"注册" if purpose == "register" else "重置密码"}验证码是：{code}\n10 分钟内有效，只能使用一次。请勿向他人透露。若非本人操作，请忽略。')
            db.commit()
    except Exception:
        logging.getLogger(__name__).warning('Email verification delivery failed; no code issued')


def verify_code(db, email, purpose, code):
    row = db.scalar(select(EmailCode).where(EmailCode.email == email, EmailCode.purpose == purpose).with_for_update())
    if row is None or row.expires <= int(time.time()) or row.attempts >= 5:
        raise HTTPException(400, '验证码无效或已过期，请重新获取')
    if not hmac.compare_digest(row.digest, digest(email, purpose, code)):
        row.attempts += 1
        db.commit()  # Failed guesses must persist even though the request fails.
        raise HTTPException(400, '验证码无效或已过期，请重新获取')
    db.delete(row)  # Consumed atomically with account creation/password change.


def notify_reset(email):
    try:
        send_mail(email, '学术助手：密码已重置', '你的应用密码已重置，原有登录会话已退出。如非本人操作，请立即重新找回密码。')
    except Exception:
        logging.getLogger(__name__).warning('Password reset notification delivery failed')
