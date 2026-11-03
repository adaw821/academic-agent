"""Opt-in, single-process desktop login helper. Never expose browser cookies to the UI."""
import os
import sys
import threading
import time
from dataclasses import dataclass, field

from app.services.ispace import ISpaceClient, SchoolError, SchoolLoginRequired
from app.services.school_sessions import save_school_session

ACTIVE = {'opening', 'waiting', 'validating'}


def enabled():
    return os.environ.get('ISPACE_LOCAL_LOGIN') == '1'


@dataclass
class Login:
    user_id: int
    state: str = 'opening'
    message: str = '正在打开学校登录窗口…'
    cancel: threading.Event = field(default_factory=threading.Event)


class LoginManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.job = None
        self.worker = None

    def status(self, user_id):
        with self.lock:
            job = self.job
            if job is None or job.user_id != user_id:
                return {'state': 'idle', 'message': ''}
            return {'state': job.state, 'message': job.message}

    def start(self, user_id):
        with self.lock:
            if self.worker and self.worker.is_alive():
                if self.job.user_id == user_id and self.job.state in ACTIVE:
                    return self.status(user_id)
                raise ValueError('登录窗口正在使用或关闭中，请稍后重试')
            self.job = Login(user_id)
            self.worker = threading.Thread(target=self.run, args=(self.job,), daemon=True)
            self.worker.start()
            return self.status(user_id)

    def cancel(self, user_id):
        with self.lock:
            if self.job and self.job.user_id == user_id:
                self.job.cancel.set()
                self.job.state, self.job.message = 'cancelled', '已取消学校连接。'
            return self.status(user_id)

    def update(self, job, state, message):
        with self.lock:
            if not job.cancel.is_set():
                job.state, job.message = state, message

    def accept(self, job, cookie):
        # Serialize saving with cancellation/disconnection: a cancelled job cannot save later.
        with self.lock:
            if job.cancel.is_set():
                return False
            save_school_session(job.user_id, cookie)
            job.state, job.message = 'connected', '学校连接已验证并保存，可以同步课程了。'
            return True

    def run(self, job):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                # Windows commonly has Edge already; Chromium remains the portable fallback.
                try:
                    browser = playwright.chromium.launch(headless=False, **({'channel': 'msedge'} if sys.platform == 'win32' else {}))
                except Exception:
                    if sys.platform != 'win32':
                        raise
                    browser = playwright.chromium.launch(headless=False)
                try:
                    context = browser.new_context()
                    page = context.new_page()
                    page.goto('https://ispace.bnbu.edu.cn/login/index.php', wait_until='domcontentloaded', timeout=60000)
                    deadline = time.monotonic() + 300
                    self.update(job, 'waiting', '请在新窗口完成学校登录，完成后会自动连接。')
                    while not job.cancel.is_set() and time.monotonic() < deadline:
                        if not browser.is_connected() or not context.pages:
                            self.update(job, 'cancelled', '登录窗口已关闭，可重新连接。')
                            return
                        cookies = context.cookies('https://ispace.bnbu.edu.cn/')
                        cookie = next((c['value'] for c in cookies if c['name'] == 'MoodleSession'), None)
                        if cookie:
                            try:
                                self.update(job, 'validating', '正在确认学校登录状态…')
                                with ISpaceClient(cookie) as client:
                                    client.discover_courses()
                                self.accept(job, cookie)
                                return
                            except SchoolLoginRequired:
                                self.update(job, 'waiting', '请在新窗口完成学校登录。')
                            except SchoolError:
                                self.update(job, 'waiting', '暂时无法验证学校访问，将自动重试…')
                        # Playwright pumps browser close events while waiting.
                        page.wait_for_timeout(2000)
                    self.update(job, 'expired', '登录等待已超时，请重新连接。')
                finally:
                    browser.close()
        except Exception:
            # Browser/HTTP exceptions can contain URLs or credentials; do not return/log them.
            self.update(job, 'failed', '登录窗口不可用或已关闭，请重试或使用手动连接。')


login_manager = LoginManager()
