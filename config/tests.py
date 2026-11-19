from datetime import timedelta

from django.conf import settings
from django.test import SimpleTestCase
from django.utils import timezone


class LocalizationSettingsTest(SimpleTestCase):
    """Guards the store's language and time zone against silent regressions.

    Both are easy to break by accident: Django's startproject template ships
    `en-us`/`UTC`, and a later refactor of settings.py could bring them back
    without any visible symptom until an order shows a date in the wrong day.
    """

    def test_language_is_argentinian_spanish(self):
        self.assertEqual(settings.LANGUAGE_CODE, "es-ar")

    def test_local_time_is_argentina_time(self):
        # Argentina has used UTC-3 without daylight saving since 2009, so the
        # offset is a stable assertion and not a seasonal one.
        self.assertEqual(timezone.localtime().utcoffset(), timedelta(hours=-3))
