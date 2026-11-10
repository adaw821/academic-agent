"""Authenticated encryption for retrievable school credentials, never app passwords."""
import base64
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import dotenv_values
from app.config import ENV_FILE


def key_path():
    # A running reload supervisor may predate local key setup, or retain an empty
    # setting. Read only this setting afresh; never override a nonblank deployment
    # path or silently switch keys when the configured file cannot be read.
    configured = os.environ.get('SCHOOL_KEY_FILE', '').strip()
    if not configured:
        configured = (dotenv_values(ENV_FILE, encoding='utf-8-sig', interpolate=False)
                      .get('SCHOOL_KEY_FILE') or '').strip()
    if not configured:
        raise ValueError('missing school key path')
    return Path(configured).resolve()


def cipher():
    try:
        path = key_path()
        root = Path(__file__).resolve().parents[2]
        if path.is_relative_to(root):
            raise ValueError('key must be outside the repository')
        key = base64.b64decode(path.read_bytes().strip(), validate=True)
        if len(key) != 32:
            raise ValueError('invalid key')
        return AESGCM(key)
    except Exception:
        raise ValueError('学校凭据加密密钥未配置或不可用') from None


def seal(user_id, data):
    nonce = os.urandom(12)
    payload = json.dumps(data, ensure_ascii=False).encode()
    encrypted = cipher().encrypt(nonce, payload, f'school:v1:{int(user_id)}'.encode())
    return base64.b64encode(nonce + encrypted).decode()


def unseal(user_id, value):
    try:
        raw = base64.b64decode(value, validate=True)
        return json.loads(cipher().decrypt(raw[:12], raw[12:], f'school:v1:{int(user_id)}'.encode()))
    except Exception:
        raise ValueError('学校凭据无法解密，请检查密钥或重新连接') from None
