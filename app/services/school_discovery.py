"""Bounded Moodle course discovery using its fixed, read-only Ajax method."""
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.services.ispace import ORIGIN, SchoolError, SchoolLoginRequired, activity_url, label_text, soup_page

METHOD = 'core_course_get_enrolled_courses_by_timeline_classification'
PAGE_SIZE = 50
MAX_PAGES = 21  # Includes the final empty page when there are exactly 1000 courses.
MAX_COURSES = 1000
AUTH_ERRORS = {'invalidsesskey', 'requireloginerror', 'servicerequireslogin', 'notloggedin', 'sessionerror'}


def page_session_key(soup):
    # Parse JSON data only; never evaluate JavaScript from the school page.
    for script in soup.find_all('script'):
        match = re.search(r'\bM\.cfg\s*=\s*', script.get_text())
        if not match:
            continue
        try:
            config, _ = json.JSONDecoder().raw_decode(script.get_text()[match.end():])
        except ValueError:
            continue
        key = config.get('sesskey') if isinstance(config, dict) else None
        if isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9]{1,128}', key):
            return key
    return None


def fetch_page(client, key, offset, *, current_only=False):
    try:
        response = client.client.post(ORIGIN + '/lib/ajax/service.php', params={'sesskey': key}, json=[{
            'index': 0, 'methodname': METHOD,
            'args': {'classification': 'inprogress' if current_only else 'allincludinghidden', 'limit': PAGE_SIZE,
                     'offset': offset, 'sort': 'fullname ASC, id ASC'},
        }])
    except httpx.RequestError:
        raise SchoolError('动态课程请求失败，列表可能不完整') from None
    if response.status_code in {401, 403} or (
        response.is_redirect and urlsplit(response.headers.get('location', '')).path.startswith('/login/')
    ):
        raise SchoolLoginRequired('学校会话已失效，请重新连接')
    # Never follow redirects with the session key or echo upstream payloads.
    if response.status_code != 200:
        raise SchoolError('动态课程接口不可用，列表可能不完整')
    if 'html' in response.headers.get('content-type', '').lower():
        soup_page(response.text)
        raise SchoolError('动态课程接口返回页面，列表可能不完整')
    try:
        payload = response.json()
    except ValueError:
        raise SchoolError('动态课程响应格式无效，列表可能不完整') from None
    item = payload[0] if isinstance(payload, list) and len(payload) == 1 else payload
    if not isinstance(item, dict):
        raise SchoolError('动态课程响应格式无效，列表可能不完整')
    if item.get('error'):
        error = item.get('exception', item)
        if isinstance(error, dict) and error.get('errorcode') in AUTH_ERRORS:
            raise SchoolLoginRequired('学校会话已失效，请重新连接')
        raise SchoolError('学校不支持当前课程查询，列表可能不完整')
    data = item.get('data')
    if item.get('error') is not False or not isinstance(data, dict):
        raise SchoolError('动态课程响应格式无效，列表可能不完整')
    rows, nextoffset = data.get('courses'), data.get('nextoffset')
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE or type(nextoffset) is not int or nextoffset < offset:
        raise SchoolError('动态课程分页格式无效，列表可能不完整')
    courses = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0 or not isinstance(row.get('fullname'), str):
            raise SchoolError('动态课程条目格式无效，列表可能不完整')
        title = BeautifulSoup(row['fullname'], 'html.parser').get_text(' ', strip=True)
        if not title:
            raise SchoolError('动态课程条目缺少标题，列表可能不完整')
        ident = str(row['id'])
        courses[ident] = dict(external_course_id=ident, title=title, source_url=ORIGIN + '/course/view.php?id=' + ident)
    return courses, nextoffset


def discover_courses(client, *, current_only=False):
    soup = soup_page(client.get_html(ORIGIN + '/my/courses.php'))
    found = {}
    for anchor in soup.select('a[href*="/course/view.php"]'):
        try:
            url = activity_url(anchor['href'], '/course/view.php')
        except SchoolError:
            continue
        ident = parse_qs(urlsplit(url).query)['id'][0]
        found[ident] = dict(source_url=url, external_course_id=ident, title=label_text(anchor))
        if len(found) >= MAX_COURSES:
            break
    key = page_session_key(soup)
    warning = '页面缺少动态课程令牌，仅返回 HTML 中发现的课程'
    dynamic, offset = {}, 0
    if key:
        warning = '达到课程分页上限，列表可能不完整'
        for _ in range(MAX_PAGES):
            try:
                courses, nextoffset = fetch_page(client, key, offset, current_only=current_only)
            except SchoolLoginRequired:
                raise
            except SchoolError as exc:
                warning = str(exc)
                break
            if not courses and nextoffset == offset:
                # The enrolled-course API is authoritative, not unrelated page links.
                return dict(courses=list(dynamic.values()), complete=True, warning=None)
            if nextoffset <= offset or (courses and not courses.keys() - dynamic.keys()):
                warning = '课程分页没有前进或重复返回同一页，列表可能不完整'
                break
            if len(dynamic.keys() | courses.keys()) > MAX_COURSES:
                warning = '达到课程数量上限，列表可能不完整'
                break
            dynamic.update(courses)
            offset = nextoffset
    # Prefer validated API names over potentially stale HTML titles on partial results.
    combined = dict(dynamic) if current_only else dict(found, **dynamic)
    return dict(courses=list(combined.values())[:MAX_COURSES], complete=False, warning=warning)
