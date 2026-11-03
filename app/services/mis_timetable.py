"""Desktop MIS capture with per-user, atomic offline snapshots."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
import sys
from urllib.parse import urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup
from app.services.school_login import enabled

URL = 'https://mis.bnbu.edu.cn/mis/student/tts/timetable.do'
ROOT = Path(__file__).resolve().parents[2] / '.local' / 'mis-timetables'
ACTIVE = {'opening', 'waiting', 'ready', 'saving'}


class TimetableCaptureError(ValueError):
    """Safe, user-facing validation errors; never include page content."""


def parse_timetable(html):
    soup = BeautifulSoup(html, 'html.parser')
    grid = soup.select_one('#mytimetable')
    term = soup.select_one('#semesterList option[selected]')
    if grid is None or term is None:
        raise TimetableCaptureError('未找到完整课表及学期，请先进入 My Timetable')
    # Layout tables can contain the entire page. Inspect each table's own rows.
    def own_rows(table):
        return [row for row in table.select('tr') if row.find_parent('table') is table]

    details = next((t for t in soup.select('table') if any(
        'Course Code' in row.get_text(' ', strip=True) and 'Teachers' in row.get_text(' ', strip=True)
        and not row.find('table') for row in own_rows(t))), None)
    if details is None:
        raise TimetableCaptureError('课表课程明细不可用，未覆盖已有课表')
    courses = []
    for row in own_rows(details):
        cells = row.find_all('td', recursive=False)
        if not cells:
            continue
        if len(cells) != 8:
            raise TimetableCaptureError('课表格式发生变化，未覆盖已有课表')
        values = [c.get_text(' ', strip=True) for c in cells]
        courses.append(dict(category=values[0], course_code=values[1], course_name=values[2], teachers=values[3],
                            times=list(cells[4].stripped_strings), rooms=list(cells[5].stripped_strings), units=values[6], remark=values[7]))
    if not courses:
        raise TimetableCaptureError('该学期没有课程明细，未覆盖已有课表')
    return {'term': term.get_text(' ', strip=True), 'courses': courses, 'source_url': URL,
            'saved_at': datetime.now(timezone.utc).isoformat()}


def read_snapshot(user_id):
    path = ROOT / f'{int(user_id)}.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def save_snapshot(user_id, snapshot):
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / f'{int(user_id)}.json'
    temporary = ROOT / f'.{uuid4().hex}.tmp'
    try:
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class MISCapture:
    def __init__(self):
        self.lock = threading.RLock()
        self.owner = None
        self.state = 'idle'
        self.message = ''
        self.worker = None
        self.cancelled = threading.Event()
        self.capture = threading.Event()

    def status(self, user_id):
        with self.lock:
            if self.owner != user_id:
                return {'state': 'idle', 'message': ''}
            return {'state': self.state, 'message': self.message}

    def update(self, state, message):
        with self.lock:
            if not self.cancelled.is_set():
                self.state, self.message = state, message

    def start(self, user_id):
        with self.lock:
            if self.worker and self.worker.is_alive():
                if self.owner == user_id and self.state in ACTIVE:
                    return self.status(user_id)
                raise ValueError('课表窗口正在使用或关闭，请稍后再试')
            self.owner = user_id
            self.cancelled, self.capture = threading.Event(), threading.Event()
            self.state, self.message = 'opening', '正在打开 MIS 课表窗口…'
            self.worker = threading.Thread(target=self.run, daemon=True)
            self.worker.start()
            return self.status(user_id)

    def request_capture(self, user_id):
        with self.lock:
            if self.owner != user_id or self.state != 'ready':
                raise ValueError('请先在学校窗口打开课表')
            self.capture.set()
            self.state, self.message = 'saving', '正在保存当前学期课表…'
            return self.status(user_id)

    def cancel(self, user_id):
        with self.lock:
            if self.owner == user_id and self.state in ACTIVE:
                self.cancelled.set()
                self.state, self.message = 'cancelled', '已取消，之前保存的课表仍保留。'
            return self.status(user_id)

    def persist(self, snapshot):
        with self.lock:
            if self.cancelled.is_set():
                return
            save_snapshot(self.owner, snapshot)
            self.state, self.message = 'saved', '课表已保存，之后查看无需登录 MIS。'

    def run(self):
        stage = '打开学校浏览器'
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=False, **({'channel': 'msedge'} if sys.platform == 'win32' else {}))
                except Exception:
                    browser = p.chromium.launch(headless=False)
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
                    stage = '打开学校课表页面'
                    page.goto(URL, wait_until='domcontentloaded', timeout=60000)
                    deadline = time.monotonic() + 600
                    self.update('waiting', '请在学校窗口登录 MIS 并打开 My Timetable。')
                    while not self.cancelled.is_set() and time.monotonic() < deadline:
                        if page.is_closed():
                            self.update('cancelled', '窗口已关闭，之前保存的课表仍保留。')
                            return
                        location = urlsplit(page.url)
                        if location.hostname == 'mis.bnbu.edu.cn' and location.path == '/mis/usr/index.do' and not page.locator('#mytimetable').count():
                            page.goto(URL, wait_until='domcontentloaded', timeout=60000)
                        ready = urlsplit(page.url).hostname == 'mis.bnbu.edu.cn' and page.locator('#mytimetable').is_visible()
                        if ready and self.capture.is_set():
                            # Extract only visible timetable data, not profile/credentials.
                            stage = '读取学期及课程明细'
                            page.wait_for_load_state('domcontentloaded', timeout=15000)
                            before = page.locator('#semesterList').input_value()
                            term = page.locator('#semesterList option:checked').inner_text()
                            tables = page.locator('table').evaluate_all('(items) => items.map(t => t.outerHTML).join("")')
                            from html import escape
                            # The grid ID may belong to a div, not to a table.
                            html = '<div id="mytimetable"></div><select id="semesterList"><option selected>' + escape(term) + '</option></select>' + tables
                            snapshot = parse_timetable(html)
                            stage = '生成课表截图'
                            png = page.locator('#mytimetable').screenshot(timeout=30000)
                            if page.locator('#semesterList').input_value() != before:
                                raise TimetableCaptureError('保存期间学期发生变化，请重新获取')
                            if len(png) > 10 * 1024 * 1024:
                                raise TimetableCaptureError('课表截图过大，请调整窗口后重试')
                            snapshot['image'] = base64.b64encode(png).decode('ascii')
                            stage = '写入本地课表文件'
                            self.persist(snapshot)
                            return
                        if ready:
                            self.update('ready', '请选择学期，等待课表显示后，回到应用点击“保存当前学期”。')
                        elif self.state == 'ready':
                            self.update('waiting', '请重新打开 My Timetable。')
                        page.wait_for_timeout(500)
                    self.update('expired', '等待已超时，请重新打开学校窗口。')
                finally:
                    browser.close()
        except TimetableCaptureError as exc:
            self.update('failed', str(exc))
        except Exception as exc:
            # Exception text may contain page data; expose only stage and type.
            self.update('failed', f'{stage}失败（{type(exc).__name__}），旧课表已保留。请重试。')


mis_capture = MISCapture()
