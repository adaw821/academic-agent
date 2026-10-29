"""Per-user local school sessions for the development deployment."""
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from app.services.ispace import SchoolLoginRequired


def session_path(user_id: int) -> Path:
    root = Path(os.environ.get('ISPACE_SESSION_DIR', str(Path(__file__).resolve().parents[2] / '.local' / 'ispace-sessions')))
    return root / f'{int(user_id)}.json'


def save_school_session(user_id: int, value: str):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,512}', value):
        raise ValueError('invalid session')
    path = session_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        # Owner-only mode on POSIX; Windows inherits the server account directory ACL.
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump({'MoodleSession': value}, output)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def remove_school_session(user_id: int):
    session_path(user_id).unlink(missing_ok=True)


def load_school_session(user_id: int) -> str:
    try:
        data = json.loads(session_path(user_id).read_text(encoding='utf-8-sig'))
        value = data.get('MoodleSession') if isinstance(data, dict) else None
        if not isinstance(value, str) or not value.strip() or re.search(r'[\s;]', value.strip()):
            raise ValueError('invalid session')
        return value.strip()
    except (OSError, ValueError):
        raise SchoolLoginRequired('未配置有效的学校会话，请更新当前用户的本地会话文件') from None
