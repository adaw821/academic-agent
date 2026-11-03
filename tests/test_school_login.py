import unittest
from unittest.mock import patch, MagicMock

from app.services.school_login import Login, LoginManager


class SchoolLoginTests(unittest.TestCase):
    def setUp(self):
        self.manager = LoginManager()
        self.job = Login(7)
        self.manager.job = self.job

    def test_status_does_not_leak_another_users_job(self):
        self.assertEqual(self.manager.status(8), {'state': 'idle', 'message': ''})
        self.manager.cancel(8)
        self.assertFalse(self.job.cancel.is_set())

    @patch('app.services.school_login.save_school_session')
    def test_cancel_during_validation_prevents_late_save(self, save):
        self.manager.cancel(7)
        self.assertFalse(self.manager.accept(self.job, 'secret'))
        self.manager.update(self.job, 'failed', 'late error')
        save.assert_not_called()
        self.assertEqual(self.manager.status(7)['state'], 'cancelled')

    @patch('app.services.school_login.save_school_session')
    def test_success_saves_only_for_job_owner_without_returning_cookie(self, save):
        self.assertTrue(self.manager.accept(self.job, 'secret'))
        save.assert_called_once_with(7, 'secret')
        self.assertEqual(self.manager.status(7)['state'], 'connected')
        self.assertNotIn('secret', str(self.manager.status(7)))

    def test_running_job_is_reused_and_other_user_cannot_replace_it(self):
        self.manager.worker = MagicMock()
        self.manager.worker.is_alive.return_value = True
        self.assertEqual(self.manager.start(7)['state'], 'opening')
        with self.assertRaises(ValueError):
            self.manager.start(8)

    @patch('app.services.school_login.save_school_session')
    def test_browser_flow_validates_before_saving(self, save):
        with patch('playwright.sync_api.sync_playwright') as factory, patch('app.services.school_login.ISpaceClient') as client:
            browser = factory.return_value.__enter__.return_value.chromium.launch.return_value
            context = browser.new_context.return_value
            context.cookies.return_value = [{'name': 'MoodleSession', 'value': 'secret'}]
            self.manager.run(self.job)
            client.return_value.__enter__.return_value.discover_courses.assert_called_once()
            save.assert_called_once_with(7, 'secret')
            browser.close.assert_called_once()
            self.assertEqual(self.job.state, 'connected')

    @patch('app.services.school_login.save_school_session')
    def test_guest_cookie_never_saved_on_timeout(self, save):
        from app.services.ispace import SchoolLoginRequired
        with patch('playwright.sync_api.sync_playwright') as factory, patch('app.services.school_login.ISpaceClient') as client, patch('app.services.school_login.time.monotonic', side_effect=[0, 1, 301]):
            browser = factory.return_value.__enter__.return_value.chromium.launch.return_value
            browser.new_context.return_value.cookies.return_value = [{'name': 'MoodleSession', 'value': 'guest'}]
            client.return_value.__enter__.return_value.discover_courses.side_effect = SchoolLoginRequired('guest')
            self.manager.run(self.job)
            save.assert_not_called()
            self.assertEqual(self.job.state, 'expired')
            browser.close.assert_called_once()

    def test_disconnect_clears_old_success_status(self):
        self.job.state = 'connected'
        self.manager.cancel(7)
        self.assertEqual(self.manager.status(7)['state'], 'cancelled')

    def test_launch_error_is_sanitized(self):
        with patch('playwright.sync_api.sync_playwright', side_effect=RuntimeError('secret')):
            self.manager.run(self.job)
        self.assertEqual(self.job.state, 'failed')
        self.assertNotIn('secret', str(self.manager.status(7)))


if __name__ == '__main__':
    unittest.main()
