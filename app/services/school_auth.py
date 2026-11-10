"""Hidden browser authentication. No screenshots, traces, console or credential logs."""
from contextlib import contextmanager
import sys
from urllib.parse import urlsplit
from app.services.ispace import ISpaceClient, SchoolLoginRequired

ISPACE = 'https://ispace.bnbu.edu.cn/login/index.php'
MIS = 'https://mis.bnbu.edu.cn/mis/student/tts/timetable.do'
HOSTS = {'ispace.bnbu.edu.cn', 'sso.bnbu.edu.cn', 'mis.bnbu.edu.cn', 'portal.bnbu.edu.cn'}


class SchoolAuthenticationError(ValueError):
    pass


class SchoolConnectionError(ValueError):
    pass


def secure_route(route):
    url = urlsplit(route.request.url)
    if url.scheme in {'data', 'blob'}:
        return route.continue_()
    if url.hostname not in HOSTS:
        return route.abort()
    if url.scheme == 'http' and route.request.method == 'GET':
        # Some school OAuth redirects still advertise HTTP. Never transmit cookies there.
        return route.fulfill(status=307, headers={'Location': 'https:' + route.request.url[5:]})
    if url.scheme != 'https':
        return route.abort()
    route.continue_()


@contextmanager
def browser_context(state=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({'channel': 'msedge'} if sys.platform == 'win32' else {}))
        try:
            context = browser.new_context(storage_state=state, service_workers='block')
            context.route('**/*', secure_route)
            context.set_default_timeout(15000)
            yield context
        finally:
            browser.close()


def authenticate(account, password):
    """One attempt only. Challenge/invalid password never triggers an automatic retry."""
    try:
        with browser_context() as context:
            page = context.new_page()
            page.goto(ISPACE, wait_until='domcontentloaded', timeout=30000)
            page.get_by_role('link', name='BNBU SSO', exact=True).click()
            page.locator('input[name="userName"]').wait_for(state='visible')
            if urlsplit(page.url).hostname != 'sso.bnbu.edu.cn' or urlsplit(page.url).scheme != 'https':
                raise SchoolAuthenticationError('学校认证地址发生变化，暂不能自动连接')
            page.locator('input[name="userName"]').fill(account)
            page.locator('input[name="password"]').fill(password)
            page.locator('.loginbutton:visible, .login_button:visible').first.click()
            try:
                page.wait_for_url('https://ispace.bnbu.edu.cn/**', timeout=20000)
            except Exception:
                raise SchoolAuthenticationError('学校认证未完成：请核对账密；如需验证码或二次验证，当前自动连接暂不支持') from None
            cookie = next((c['value'] for c in context.cookies(ISPACE) if c['name'] == 'MoodleSession'), None)
            if not cookie:
                raise SchoolAuthenticationError('未取得有效的 ispace 会话')
            with ISpaceClient(cookie) as client:
                client.discover_courses()
            page.goto(MIS, wait_until='domcontentloaded', timeout=30000)
            if page.locator('input[name="j_username"]').count():
                page.locator('input[name="j_username"]').fill(account)
                page.locator('input[name="j_password"]').fill(password)
                page.locator('#student').check()
                page.locator('input[type="image"]').click()
                page.wait_for_load_state('domcontentloaded')
                page.goto(MIS, wait_until='domcontentloaded', timeout=30000)
            try:
                page.locator('#mytimetable').wait_for(state='visible')
            except Exception:
                raise SchoolAuthenticationError('MIS 连接未完成，请核对学生账密或学校端认证要求') from None
            return {'account': account, 'password': password, 'storage_state': context.storage_state(), 'cookie': cookie}
    except SchoolAuthenticationError:
        raise
    except Exception:
        raise SchoolConnectionError('学校连接失败，请稍后重试或检查服务器浏览器环境') from None


def fetch_timetable(state, term=None):
    from html import escape
    from app.services.mis_timetable import parse_timetable
    with browser_context(state) as context:
        page = context.new_page()
        page.goto(MIS, wait_until='domcontentloaded', timeout=30000)
        if page.locator('input[name="j_username"]').count() or urlsplit(page.url).hostname != 'mis.bnbu.edu.cn':
            raise SchoolLoginRequired('MIS 会话已失效')
        page.locator('#mytimetable').wait_for(state='visible')
        if term:
            page.locator('#semesterList').select_option(value=term)
            page.wait_for_load_state('domcontentloaded')
        chosen = page.locator('#semesterList').input_value()
        title = page.locator('#semesterList option:checked').inner_text()
        tables = page.locator('table').evaluate_all('(items) => items.map(t => t.outerHTML).join("")')
        snapshot = parse_timetable('<div id="mytimetable"></div><select id="semesterList"><option selected>' + escape(title) + '</option></select>' + tables)
        if chosen != page.locator('#semesterList').input_value() or (term and chosen != term):
            raise ValueError('学校返回的学期不一致')
        snapshot['term_id'] = chosen
        return snapshot, context.storage_state()
