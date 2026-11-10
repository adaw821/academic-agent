"""Read-only iSpace collector. Credentials are supplied explicitly by the user."""
from dataclasses import asdict, dataclass, field
from collections import deque
from datetime import datetime, timezone, timedelta
from email.message import Message
from pathlib import PurePosixPath
import hashlib
import re
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

ORIGIN = "https://ispace.bnbu.edu.cn"
CHINA = timezone(timedelta(hours=8), "Asia/Shanghai")
MAX_FOLDERS = 100
MAX_FOLDER_RESOURCES = 2000


class SchoolError(ValueError):
    pass


class SchoolLoginRequired(SchoolError):
    pass


def safe_url(value, base=ORIGIN):
    url = urlsplit(urljoin(base, value))
    if url.scheme != "https" or url.netloc != "ispace.bnbu.edu.cn":
        raise SchoolError("仅支持学校 iSpace HTTPS 地址")
    return urlunsplit((url.scheme, url.netloc, url.path, url.query, ""))


def activity_url(value, path):
    url = safe_url(value)
    parsed = urlsplit(url)
    ids = parse_qs(parsed.query).get("id", [])
    if parsed.path != path or len(ids) != 1 or not ids[0].isdigit():
        raise SchoolError("课程或活动链接格式不正确")
    return ORIGIN + path + "?" + urlencode({"id": ids[0]})


def soup_page(html):
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('input[name="password"], #login'):
        raise SchoolLoginRequired("学校登录已失效，请更新本地 MoodleSession")
    error = soup.select_one('.errorbox, [data-rel="fatalerror"]')
    if error:
        raise SchoolError("学校页面返回错误，未同步数据")
    return soup


def label_text(element):
    copy = BeautifulSoup(str(element), "html.parser")
    for hidden in copy.select('.accesshide, .sr-only'):
        hidden.decompose()
    return copy.get_text(" ", strip=True)


def parse_date(raw):
    if not raw:
        return None
    value = raw.strip()
    # Site currently displays English dates. Parse without depending on OS locale.
    months = {m: i for i, m in enumerate("January February March April May June July August September October November December".split(), 1)}
    match = re.search(r"(\d{1,2}) (\w+) (\d{4}),\s*(\d{1,2}):(\d{2})\s*(AM|PM)", value, re.I)
    if match:
        day, month, year, hour, minute, period = match.groups()
        if month.title() not in months or not 1 <= int(hour) <= 12:
            raise SchoolError("无法识别学校日期")
        hour = int(hour) % 12 + (12 if period.upper() == "PM" else 0)
        return datetime(int(year), months[month.title()], int(day), hour, int(minute), tzinfo=CHINA)
    match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日.*?(\d{1,2}):(\d{2})", value)
    if match:
        return datetime(*map(int, match.groups()), tzinfo=CHINA)
    raise SchoolError("日期格式不受支持，停止同步以避免覆盖已有 DDL")


def dates(element):
    result = {"opens_at": None, "due_at": None, "due_at_raw": None}
    for label in element.select('[data-region="activity-dates"] strong'):
        name = label.get_text(strip=True).rstrip(':：')
        raw = label.parent.get_text(" ", strip=True).removeprefix(label.get_text(" ", strip=True)).strip()
        if name in {"Due", "截止时间", "到期时间"}:
            result.update(due_at=parse_date(raw), due_at_raw=raw)
        elif name in {"Opened", "Opens", "开放时间", "开始时间"}:
            result["opens_at"] = parse_date(raw)
    return result


@dataclass
class CourseSnapshot:
    external_course_id: str
    title: str
    source_url: str
    assignments: list[dict] = field(default_factory=list)
    resources: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def resources_in(element, base):
    found = {}
    for a in element.select('a[href]'):
        try:
            url = safe_url(a['href'], base)
        except SchoolError:
            continue
        parsed = urlsplit(url)
        if '/assignsubmission_' in parsed.path:
            continue  # Student submissions are never teacher materials.
        if parsed.path in {'/mod/resource/view.php', '/mod/page/view.php', '/mod/folder/view.php'}:
            url = activity_url(url, parsed.path)
            kind = parsed.path.split('/')[2]
        elif parsed.path.startswith('/pluginfile.php/'):
            # These links are file resources, not actions; discard token/query parameters.
            url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
            kind = 'file'
        else:
            continue
        key = hashlib.sha256(url.encode()).hexdigest()
        found[key] = dict(source_key=key, title=label_text(a) or parsed.path.rsplit('/', 1)[-1],
                          source_url=url, resource_type=kind, purpose='unknown')
    return list(found.values())


def parse_course(html, url):
    url = activity_url(url, '/course/view.php')
    soup = soup_page(html)
    region = soup.select_one('#region-main')
    heading = soup.select_one('h1')
    if region is None or heading is None or not soup.select_one('[id^="section-"], .course-content'):
        raise SchoolError("未识别到课程正文，可能没有访问权限或页面结构已改变")
    result = CourseSnapshot(parse_qs(urlsplit(url).query)['id'][0], heading.get_text(' ', strip=True), url)
    for item in region.select('li.activity'):
        a = item.select_one('a[href*="/mod/assign/view.php"]')
        if a is None:
            continue
        source = activity_url(a['href'], '/mod/assign/view.php')
        description = item.select_one('.activity-description')
        result.assignments.append(dict(
            external_activity_id=parse_qs(urlsplit(source).query)['id'][0],
            title=label_text(a), source_url=source,
            description=description.get_text(' ', strip=True) if description else None,
            source_timezone='Asia/Shanghai', **dates(item),
        ))
    result.resources = resources_in(region, url)
    result.warnings.append('仅同步当前账号可见内容；不删除本次未出现的旧记录。')
    return result


def parse_assignment(html):
    soup = soup_page(html)
    region = soup.select_one('#region-main')
    if region is None or region.select_one('h2') is None:
        raise SchoolError("未识别到作业详情")
    result = dates(region)
    result.update(submission_status=None, grading_status=None)
    for row in region.select('tr'):
        cells = row.find_all(['th', 'td'], recursive=False)
        if len(cells) != 2:
            continue
        key = {'Submission status': 'submission_status', 'Grading status': 'grading_status',
               '提交状态': 'submission_status', '评分状态': 'grading_status'}.get(cells[0].get_text(' ', strip=True))
        if key:
            result[key] = cells[1].get_text(' ', strip=True)
    intro = region.select_one('#intro')
    result['description'] = intro.get_text(' ', strip=True) if intro else None
    return result, resources_in(intro, ORIGIN) if intro else []


def parse_folder(html, url):
    url = activity_url(url, '/mod/folder/view.php')
    soup = soup_page(html)
    trees = soup.select('#region-main .foldertree')
    if not trees:
        raise SchoolError('未识别到 Folder 文件树，停止同步以避免遗漏附件')
    found = {}
    # Subdirectories are nested HTML, including collapsed/hidden branches.
    for tree in trees:
        for item in resources_in(tree, url):
            if item['resource_type'] in {'file', 'folder'}:
                found.setdefault(item['source_key'], item)
    return list(found.values())


class ISpaceClient:
    def __init__(self, session_cookie, *, transport=None):
        if not session_cookie or re.search(r'[\s;\r\n]', session_cookie):
            raise SchoolError('需要有效的 MoodleSession 值')
        self.client = httpx.Client(timeout=30, follow_redirects=False, transport=transport)
        self.client.cookies.set('MoodleSession', session_cookie, domain='ispace.bnbu.edu.cn', path='/')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def download_resource(self, url, destination, *, max_bytes=50 * 1024 * 1024):
        """Stream one teacher attachment to a caller-owned temporary file."""
        for _ in range(5):
            url = safe_url(url)
            path = unquote(urlsplit(url).path)
            if path.startswith('/login/'):
                raise SchoolLoginRequired('学校登录已失效')
            is_file = path.startswith('/pluginfile.php/')
            if ('assignsubmission_' in path or '..' in path.split('/') or
                    (not is_file and path != '/mod/resource/view.php')):
                raise SchoolError('不支持的附件地址')
            if not is_file:
                url = activity_url(url, '/mod/resource/view.php')
            try:
                with self.client.stream('GET', url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get('location')
                        if not location:
                            raise SchoolError('附件跳转缺少地址')
                        url = safe_url(location, url)
                        continue
                    if response.status_code in {401, 403}:
                        raise SchoolLoginRequired('学校会话失效或无权访问附件')
                    if response.status_code != 200:
                        raise SchoolError('学校附件下载失败')
                    content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
                    # Resource activities may embed the actual file in an HTML page.
                    if 'html' in content_type:
                        body = bytearray()
                        for chunk in response.iter_bytes(64 * 1024):
                            body.extend(chunk)
                            if len(body) > min(max_bytes, 2 * 1024 * 1024):
                                raise SchoolError('附件页面过大')
                        soup = soup_page(bytes(body))
                        if is_file:
                            raise SchoolError('附件返回 HTML，未保存为文档')
                        links = set()
                        for node in soup.select('object[data], iframe[src], .resourceworkaround a[href]'):
                            candidate = safe_url(node.get('data') or node.get('src') or node.get('href'), url)
                            if urlsplit(candidate).path.startswith('/pluginfile.php/'):
                                links.add(candidate)
                        if len(links) != 1:
                            raise SchoolError('无法确定唯一附件链接')
                        url = links.pop()
                        continue
                    header = Message()
                    header['content-disposition'] = response.headers.get('content-disposition', '')
                    filename = header.get_filename() or unquote(urlsplit(url).path.rsplit('/', 1)[-1])
                    filename = filename.replace('\\', '/').rsplit('/', 1)[-1]
                    suffix = PurePosixPath(filename).suffix.lower()
                    if suffix not in {'.pdf', '.docx', '.pptx', '.txt'}:
                        raise SchoolError('仅支持 PDF、DOCX、PPTX、TXT 附件')
                    length = response.headers.get('content-length', '')
                    if length.isdigit() and int(length) > max_bytes:
                        raise SchoolError('附件超过大小限制')
                    size = 0
                    with destination.open('xb') as output:
                        for chunk in response.iter_bytes(64 * 1024):
                            if size == 0 and chunk.lstrip().lower().startswith((b'<!doctype html', b'<html', b'<form')):
                                raise SchoolError('附件内容是 HTML，未保存为文档')
                            size += len(chunk)
                            if size > max_bytes:
                                raise SchoolError('附件超过大小限制')
                            output.write(chunk)
                    if not size:
                        raise SchoolError('附件为空')
                    return dict(filename=filename, suffix=suffix, content_type=content_type or 'application/octet-stream', size_bytes=size)
            except httpx.RequestError:
                raise SchoolError('附件下载中断，请重试') from None
        raise SchoolError('附件跳转次数过多')

    def get_html(self, url):
        url = safe_url(url)
        # Fixed read-only routes only, even when following school redirects.
        allowed = {'/course/view.php', '/mod/assign/view.php', '/mod/page/view.php', '/mod/folder/view.php', '/my/', '/my/courses.php'}
        for _ in range(5):
            if urlsplit(url).path not in allowed:
                raise SchoolLoginRequired('学校跳转到登录或不支持的页面，请重新登录')
            try:
                response = self.client.get(url)
            except httpx.RequestError:
                raise SchoolError('学校请求失败，请检查网络后重试') from None
            if response.status_code in {301, 302, 303, 307, 308}:
                url = safe_url(response.headers.get('location', ''), url)
                continue
            if response.status_code in {401, 403}:
                raise SchoolLoginRequired('学校会话失效或无权访问该课程')
            if response.status_code != 200:
                raise SchoolError(f'学校返回 HTTP {response.status_code}，未同步')
            if 'html' not in response.headers.get('content-type', ''):
                raise SchoolError('学校返回的内容不是 HTML')
            soup_page(response.text)
            return response.text
        raise SchoolError('学校跳转次数过多')

    def discover_courses(self, *, current_only=False):
        from app.services.school_discovery import discover_courses
        return discover_courses(self, current_only=current_only)

    def collect_course(self, url):
        url = activity_url(url, '/course/view.php')
        snapshot = parse_course(self.get_html(url), url)
        if len(snapshot.assignments) > 200:
            raise SchoolError('课程规模超出单次同步限制')
        resources = {r['source_key']: r for r in snapshot.resources}
        assignment_refs = {}
        for assignment in snapshot.assignments:
            detail, attachments = parse_assignment(self.get_html(assignment['source_url']))
            # Detail view is authoritative, including a removed deadline.
            assignment.update(detail)
            for item in attachments:
                resources.setdefault(item['source_key'], item)
                if item['resource_type'] in {'file', 'resource'}:
                    assignment_refs.setdefault(item['source_key'], set()).add(assignment['external_activity_id'])
        # Follow one layer of teacher-created Page resources (Lectures, Lab).
        pages = [r for r in resources.values() if r['resource_type'] == 'page']
        if len(pages) > 100 or len(snapshot.assignments) > 200:
            raise SchoolError('课程规模超出单次同步限制')
        for page in pages:
            soup = soup_page(self.get_html(page['source_url']))
            content = soup.select_one('#region-main')
            if content is None:
                raise SchoolError('未识别到课程资源页面')
            page['content_text'] = content.get_text(' ', strip=True)
            for item in resources_in(content, page['source_url']):
                resources.setdefault(item['source_key'], item)
        folders = deque(r for r in resources.values() if r['resource_type'] == 'folder')
        scheduled = {r['source_key'] for r in folders}
        folder_resources = set()
        while folders:
            if len(scheduled) > MAX_FOLDERS:
                raise SchoolError('Folder 数量超出单次同步限制')
            folder = folders.popleft()
            for item in parse_folder(self.get_html(folder['source_url']), folder['source_url']):
                key = item['source_key']
                folder_resources.add(key)
                if len(folder_resources) > MAX_FOLDER_RESOURCES:
                    raise SchoolError('Folder 资源数量超出单次同步限制')
                resources.setdefault(key, item)
                if item['resource_type'] == 'folder' and key not in scheduled:
                    scheduled.add(key)
                    folders.append(item)
        snapshot.resources = list(resources.values())
        for item in snapshot.resources:
            # Transient provenance consumed by sync_course, not a database column.
            item['assignment_external_ids'] = sorted(assignment_refs.get(item['source_key'], set()))
        snapshot.warnings.append('已收集资源链接、一层 Page 内容及 Folder 文件树附件；附件需单独下载并导入项目。')
        return snapshot

    def collect_assignments(self, url):
        """Read-only: a course's assignments with per-assignment due/status only.

        Lighter than collect_course (skips resources, Pages and Folder trees) so a
        live peek stays fast. Never writes; the caller diffs against local data.
        """
        url = activity_url(url, '/course/view.php')
        snapshot = parse_course(self.get_html(url), url)
        if len(snapshot.assignments) > 200:
            raise SchoolError('课程规模超出单次读取限制')
        for assignment in snapshot.assignments:
            detail, _ = parse_assignment(self.get_html(assignment['source_url']))
            # Detail view is authoritative, including a removed deadline.
            assignment.update(detail)
        snapshot.resources = []
        return snapshot
