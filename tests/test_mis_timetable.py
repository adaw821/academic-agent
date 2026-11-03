import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from app.services import mis_timetable as mis

HTML = '''<select id="semesterList"><option selected>Semester 1</option></select>
<table id="mytimetable"><tr><th>Time</th><th>Mon</th></tr></table>
<table><tr><th>Course Code</th><th>Teachers</th></tr>
<tr><th>1</th><td>MR</td><td>TEST1001</td><td>Example Course (1001)</td><td>Teacher</td><td>Mon 8:00-9:50<br>Thu 13:00-13:50</td><td>A101<br>B201</td><td>3.0</td><td></td></tr>
<tr><th>2</th><td>MR</td><td>TEST1002</td><td>Project</td><td>Teacher</td><td></td><td></td><td>3.0</td><td>No fixed time</td></tr></table>'''


class MISTimetableTests(unittest.TestCase):
    def test_parses_observed_mis_structure_without_losing_unscheduled_courses(self):
        data = mis.parse_timetable(HTML)
        self.assertEqual(data['term'], 'Semester 1')
        self.assertEqual(len(data['courses']), 2)
        self.assertEqual(data['courses'][0]['times'], ['Mon 8:00-9:50', 'Thu 13:00-13:50'])
        self.assertEqual(data['courses'][0]['rooms'], ['A101', 'B201'])
        self.assertEqual(data['courses'][1]['times'], [])

    def test_login_empty_or_changed_page_is_rejected(self):
        for html in ('<form>Login</form>', HTML.replace('id="mytimetable"', ''), HTML.replace('<td>3.0</td>', '')):
            with self.subTest(html=html[:20]), self.assertRaises(ValueError):
                mis.parse_timetable(html)

    def test_layout_table_does_not_hide_course_details(self):
        data = mis.parse_timetable('<table><tr><td>' + HTML + '</td></tr></table>')
        self.assertEqual(len(data['courses']), 2)
        self.assertEqual(data['courses'][0]['course_code'], 'TEST1001')

    def test_capture_with_div_grid_and_background_network_activity(self):
        manager = mis.MISCapture()
        manager.owner, manager.state = 1, 'ready'
        manager.capture.set()
        with patch('playwright.sync_api.sync_playwright') as factory, patch.object(manager, 'persist') as persist:
            browser = factory.return_value.__enter__.return_value.chromium.launch.return_value
            page = browser.new_page.return_value
            page.url = mis.URL
            page.is_closed.return_value = False
            # evaluate_all returns tables only, without the surrounding grid div.
            page.locator.return_value.evaluate_all.return_value = HTML.replace('id="mytimetable"', '')
            page.locator.return_value.input_value.return_value = '1'
            page.locator.return_value.inner_text.return_value = 'Semester 1'
            page.locator.return_value.screenshot.return_value = b'png'
            def wait(state, **kwargs):
                if state == 'networkidle':
                    raise TimeoutError('background requests never stop')
            page.wait_for_load_state.side_effect = wait
            manager.run()
            persist.assert_called_once()
            self.assertEqual(len(persist.call_args.args[0]['courses']), 2)
            browser.close.assert_called_once()

    def test_parse_failure_is_specific_and_preserves_snapshot(self):
        manager = mis.MISCapture()
        manager.owner, manager.state = 1, 'ready'
        manager.capture.set()
        with patch('playwright.sync_api.sync_playwright') as factory, patch.object(manager, 'persist') as persist:
            page = factory.return_value.__enter__.return_value.chromium.launch.return_value.new_page.return_value
            page.url = mis.URL
            page.is_closed.return_value = False
            page.locator.return_value.evaluate_all.return_value = '<table></table>'
            page.locator.return_value.inner_text.return_value = 'Semester 1'
            manager.run()
            self.assertEqual(manager.state, 'failed')
            self.assertIn('课程明细不可用', manager.message)
            persist.assert_not_called()

    def test_saved_snapshot_is_user_scoped_and_cancel_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(mis, 'ROOT', Path(folder)):
            original = dict(mis.parse_timetable(HTML), image='cG5n')
            mis.save_snapshot(1, original)
            self.assertIsNone(mis.read_snapshot(2))
            manager = mis.MISCapture()
            manager.owner, manager.state = 1, 'saving'
            manager.cancel(1)
            manager.persist(dict(original, term='changed'))
            self.assertEqual(mis.read_snapshot(1), original)
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_other_user_cannot_capture_cancel_or_view_job(self):
        manager = mis.MISCapture()
        manager.owner, manager.state = 1, 'ready'
        self.assertEqual(manager.status(2)['state'], 'idle')
        manager.cancel(2)
        self.assertFalse(manager.cancelled.is_set())
        with self.assertRaises(ValueError):
            manager.request_capture(2)
        self.assertEqual(manager.request_capture(1)['state'], 'saving')
        self.assertTrue(manager.capture.is_set())

    def test_failure_during_write_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(mis, 'ROOT', Path(folder)):
            mis.save_snapshot(1, {'term': 'old'})
            with patch.object(Path, 'replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    mis.save_snapshot(1, {'term': 'new'})
            self.assertEqual(mis.read_snapshot(1), {'term': 'old'})


if __name__ == '__main__':
    unittest.main()
