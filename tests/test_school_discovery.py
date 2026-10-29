"""Mock Moodle Ajax responses; no school credentials or database required."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from app.services.ispace import ISpaceClient, ORIGIN, SchoolLoginRequired
from app.services.school_discovery import METHOD

HTML = '<script>M.cfg = {"sesskey":"TestKey123","wwwroot":"https://wrong.example"};</script>'
LINK = '<a href="/course/view.php?id=7">Old HTML title</a>'


def page(courses=(), nextoffset=0):
    return httpx.Response(200, json=[{'error': False, 'data': {'courses': list(courses), 'nextoffset': nextoffset}}])


class CourseDiscoveryTests(unittest.TestCase):
    def discover(self, responses=(), html=HTML):
        self.requests = []
        replies = iter(responses)
        def handler(request):
            self.requests.append(request)
            self.assertEqual(request.url.host, 'ispace.bnbu.edu.cn')
            self.assertEqual(request.headers['cookie'], 'MoodleSession=test')
            if request.method == 'GET':
                self.assertEqual(request.url.path, '/my/courses.php')
                return httpx.Response(200, text=html, headers={'content-type': 'text/html'})
            self.assertEqual(request.method, 'POST')
            self.assertEqual(request.url.path, '/lib/ajax/service.php')
            self.assertEqual(request.url.params['sesskey'], 'TestKey123')
            request_data = json.loads(request.content)
            self.assertEqual(len(request_data), 1)
            self.assertEqual(request_data[0]['methodname'], METHOD)
            self.assertEqual(request_data[0]['args']['classification'], 'allincludinghidden')
            result = next(replies)
            if isinstance(result, Exception):
                raise result
            return result
        with ISpaceClient('test', transport=httpx.MockTransport(handler)) as client:
            return client.discover_courses()

    def offsets(self):
        return [json.loads(request.content)[0]['args']['offset'] for request in self.requests if request.method == 'POST']

    def test_dynamic_pages_use_server_offset_and_normalize_courses(self):
        result = self.discover([
            page([{'id': 7, 'fullname': '<b>Database</b> &amp; SQL', 'viewurl': 'https://wrong.example'}], 11),
            page([{'id': 8, 'fullname': 'Algorithms'}], 17),
            page([], 17),
        ], HTML + LINK + '<a href="/course/view.php?id=999">Unrelated navigation</a>')
        self.assertTrue(result['complete'])
        self.assertIsNone(result['warning'])
        self.assertEqual(self.offsets(), [0, 11, 17])
        self.assertEqual([row['external_course_id'] for row in result['courses']], ['7', '8'])
        self.assertEqual(result['courses'][0]['title'], 'Database & SQL')
        self.assertEqual(result['courses'][0]['source_url'], ORIGIN + '/course/view.php?id=7')
        self.assertNotIn('TestKey123', json.dumps(result))

    def test_empty_page_with_advanced_offset_does_not_end_early(self):
        result = self.discover([page([], 4), page([{'id': 8, 'fullname': 'Next'}], 5), page([], 5)])
        self.assertTrue(result['complete'])
        self.assertEqual(self.offsets(), [0, 4, 5])

    def test_empty_enrolments_are_complete(self):
        result = self.discover([page()], HTML + LINK)
        self.assertTrue(result['complete'])
        self.assertEqual(result['courses'], [])

    def test_missing_or_non_json_config_falls_back_without_evaluating_scripts(self):
        for config in ('', '<script>M.cfg = evil();</script>', '<script>M.cfg={"sesskey":"bad;key"};</script>'):
            with self.subTest(config=config):
                result = self.discover(html=config + LINK + '<a href="https://outside.example/course/view.php?id=9">Outside</a><a href="/course/view.php?id=bad">Bad</a>')
                self.assertFalse(result['complete'])
                self.assertEqual(len(result['courses']), 1)
                self.assertEqual(len(self.requests), 1)

    def test_unsupported_method_and_network_failure_preserve_partial_courses(self):
        for error in (httpx.Response(200, json=[{'error': True, 'exception': {'errorcode': 'invalidparameter', 'message': 'private upstream data'}}]),
                      httpx.ReadTimeout('private upstream data'), httpx.Response(503)):
            with self.subTest(error=type(error).__name__):
                result = self.discover([page([{'id': 7, 'fullname': 'New title'}], 1), error], HTML + LINK)
                self.assertFalse(result['complete'])
                self.assertEqual(result['courses'][0]['title'], 'New title')
                self.assertNotIn('private upstream data', json.dumps(result))

    def test_auth_failures_are_not_reported_as_successful_partial_results(self):
        responses = [httpx.Response(401), httpx.Response(403),
                     httpx.Response(302, headers={'location': '/login/index.php'}),
                     httpx.Response(200, text='<input name="password">', headers={'content-type': 'text/html'}),
                     httpx.Response(200, json=[{'error': True, 'exception': {'errorcode': 'invalidsesskey'}}]),
                     httpx.Response(200, json={'error': True, 'errorcode': 'requireloginerror'})]
        for response in responses:
            with self.subTest(status=response.status_code), self.assertRaises(SchoolLoginRequired):
                self.discover([response])

    def test_cross_host_redirect_is_not_followed(self):
        result = self.discover([httpx.Response(302, headers={'location': 'https://other.example/service.php'})], HTML + LINK)
        self.assertFalse(result['complete'])
        self.assertEqual(len(self.requests), 2)

    def test_bad_shapes_and_course_rows_never_claim_completeness(self):
        responses = [httpx.Response(200, text='not json'), httpx.Response(200, json=[]),
                     httpx.Response(200, json=[{'error': False, 'data': {}}]),
                     page([{'id': True, 'fullname': 'bad'}], 1), page([{'id': 1, 'fullname': None}], 1),
                     page([{'id': 1, 'fullname': ''}], 1), page([], -1), page([], True)]
        for response in responses:
            with self.subTest(response=response.content):
                result = self.discover([response], HTML + LINK)
                self.assertFalse(result['complete'])
                self.assertEqual(len(result['courses']), 1)

    def test_repeated_page_and_stalled_cursor_stop(self):
        for nextoffset in (1, 2):
            with self.subTest(nextoffset=nextoffset):
                result = self.discover([page([{'id': 1, 'fullname': 'One'}], 1), page([{'id': 1, 'fullname': 'One'}], nextoffset)])
                self.assertFalse(result['complete'])
                self.assertEqual(len(result['courses']), 1)
                self.assertEqual(len(self.requests), 3)

    def test_pagination_and_course_count_caps(self):
        with patch('app.services.school_discovery.MAX_PAGES', 1):
            result = self.discover([page([{'id': 1, 'fullname': 'One'}], 1)])
            self.assertFalse(result['complete'])
            self.assertEqual(len(result['courses']), 1)
        with patch('app.services.school_discovery.MAX_COURSES', 1):
            result = self.discover([page([{'id': 1, 'fullname': 'One'}], 1), page([{'id': 2, 'fullname': 'Two'}], 2)])
            self.assertFalse(result['complete'])
            self.assertEqual(len(result['courses']), 1)


if __name__ == '__main__':
    unittest.main()
