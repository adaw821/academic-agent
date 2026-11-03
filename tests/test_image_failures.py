import os
import unittest
from unittest.mock import patch
from app.services.image_failures import check_image_failures, TooManyImageFailures


class ImageFailurePolicyTests(unittest.TestCase):
    def test_boundary_and_absolute_limit(self):
        with patch.dict(os.environ, {'IMAGE_FAILURE_MIN_COUNT': '3', 'IMAGE_FAILURE_MAX_COUNT': '10', 'IMAGE_FAILURE_RATIO': '0.5'}):
            for failed, total, rejected in [(0, 0, False), (1, 1, False), (2, 2, False), (3, 6, False), (3, 5, True), (9, 30, False), (10, 100, True)]:
                with self.subTest(failed=failed, total=total):
                    summary = dict(failed=failed, total=total, failures=[])
                    if rejected:
                        with self.assertRaises(TooManyImageFailures) as error:
                            check_image_failures(summary)
                        self.assertEqual(error.exception.summary, summary)
                    else:
                        check_image_failures(summary)

    def test_invalid_threshold_is_not_silently_accepted(self):
        with patch.dict(os.environ, {'IMAGE_FAILURE_RATIO': '2'}):
            with self.assertRaises(ValueError):
                check_image_failures(dict(failed=0, total=0))
