import datetime

import pytz

from temba.tests import TembaTest
from temba.utils.legacy.dates import datetime_to_str, str_to_date, str_to_datetime, str_to_time


class LegacyDateTest(TembaTest):
    def test_datetime_to_str(self):
        tz = pytz.timezone("Africa/Kigali")
        date = datetime_to_str(None, "", tz)
        self.assertIsNone(date)

        date = tz.localize(datetime.datetime(2021, 1, 2, 3, 4, 5, 6))
        self.assertEqual(datetime_to_str(date, "%Y-%m-%d %H:%M", tz=tz), "2021-01-02 03:04")

        date = datetime.date(2021, 1, 2)
        self.assertEqual(datetime_to_str(date, "%Y-%m-%d", tz=tz), "2021-01-02")

    def test_str_to_date(self):
        self.assertIsNone(str_to_date(None))
        self.assertEqual(str_to_date("2021-10-11T04:38:09.100000+02:00"), datetime.date(2021, 10, 11))
        self.assertEqual(str_to_date("6-18-19"), datetime.date(2019, 6, 18))

    def test_str_to_datetime(self):
        tz = pytz.timezone("Africa/Kigali")
        timezone_info = datetime.timezone(datetime.timedelta(0, 7200), "+02:00")
        self.assertIsNone(str_to_datetime(None, tz))
        self.assertIsNone(str_to_datetime("2021", tz))
        self.assertEqual(
            str_to_datetime("2021-10-11T04:38:09.100000+02:00", tz),
            datetime.datetime(2021, 10, 11, 4, 38, 9, 100000, tzinfo=timezone_info),
        )
        self.assertEqual(
            str_to_datetime("2021-10-11T04:38:09", tz).replace(tzinfo=tz),
            datetime.datetime(2021, 10, 11, 4, 38, 9, tzinfo=tz),
        )
        self.assertEqual(
            str_to_datetime("2021-10-11", tz, True, False).replace(tzinfo=tz),
            datetime.datetime(2021, 10, 11, 0, 0, tzinfo=tz),
        )

    def test_str_to_time(self):
        self.assertIsNone(str_to_time(""))
        self.assertIsNone(str_to_time("2021-10-11"))
        self.assertEqual(str_to_time("2021-10-11 11:10:20"), datetime.time(11, 10, 20))
        self.assertEqual(str_to_time("11:10:20"), datetime.time(11, 10, 20))
        self.assertEqual(str_to_time("12:30 am"), datetime.time(0, 30))
        self.assertEqual(str_to_time("12:30 pm"), datetime.time(12, 30))
