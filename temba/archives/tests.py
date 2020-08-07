from datetime import date, datetime
from unittest.mock import patch

import pytz

from django.urls import reverse
from django.utils import timezone

from temba.tests import CRUDLTestMixin, TembaTest
from temba.tests.s3 import MockS3Client

from .models import Archive


class ArchiveTest(TembaTest):
    def test_iter_records(self):
        mock_s3 = MockS3Client()
        archive = self.create_archive(
            Archive.TYPE_MSG, "D", timezone.now().date(), [{"id": 1}, {"id": 2}, {"id": 3}], s3=mock_s3
        )

        with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
            records_iter = archive.iter_records()

            self.assertEqual(next(records_iter), {"id": 1})
            self.assertEqual(next(records_iter), {"id": 2})
            self.assertEqual(next(records_iter), {"id": 3})
            self.assertRaises(StopIteration, next, records_iter)

    def test_iter_records_with_expression(self):
        mock_s3 = MockS3Client()
        archive = self.create_archive(
            Archive.TYPE_MSG, "D", timezone.now().date(), [{"id": 1}, {"id": 2}, {"id": 3}], s3=mock_s3
        )

        with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
            records_iter = archive.iter_records(expression="s.direction = 'in'")

            self.assertEqual(next(records_iter), {"id": 1})
            self.assertEqual(next(records_iter), {"id": 2})
            self.assertEqual(next(records_iter), {"id": 3})
            self.assertRaises(StopIteration, next, records_iter)

    @patch("temba.archives.models.Archive.s3_client")
    def test_iter_all_records(self, mock_s3_client):
        mock_s3 = MockS3Client()
        mock_s3_client.return_value = mock_s3

        d1 = self.create_archive(
            Archive.TYPE_MSG,
            "D",
            date(2020, 7, 31),
            [{"id": 1, "created_on": "2020-07-30T10:00:00Z"}, {"id": 2, "created_on": "2020-07-30T15:00:00Z"}],
            s3=mock_s3,
        )
        self.create_archive(
            Archive.TYPE_MSG,
            "M",
            date(2020, 7, 1),
            [{"id": 1, "created_on": "2020-07-30T10:00:00Z"}, {"id": 2, "created_on": "2020-07-30T15:00:00Z"}],
            rollup_of=(d1,),
            s3=mock_s3,
        )
        self.create_archive(
            Archive.TYPE_MSG,
            "D",
            date(2020, 8, 1),
            [{"id": 3, "created_on": "2020-08-01T10:00:00Z"}, {"id": 4, "created_on": "2020-08-01T15:00:00Z"}],
            s3=mock_s3,
        )
        self.create_archive(
            Archive.TYPE_FLOWRUN,
            "D",
            date(2020, 8, 1),
            [{"id": 3, "created_on": "2020-08-01T10:00:00Z"}, {"id": 4, "created_on": "2020-08-01T15:00:00Z"}],
            s3=mock_s3,
        )
        self.create_archive(
            Archive.TYPE_MSG,
            "D",
            date(2020, 8, 2),
            [{"id": 5, "created_on": "2020-08-02T10:00:00Z"}, {"id": 6, "created_on": "2020-08-02T15:00:00Z"}],
            s3=mock_s3,
        )

        def assert_records(record_iter, ids):
            self.assertEqual(ids, [r["id"] for r in list(record_iter)])

        assert_records(Archive.iter_all_records(self.org, Archive.TYPE_MSG), [1, 2, 3, 4, 5, 6])
        assert_records(
            Archive.iter_all_records(self.org, Archive.TYPE_MSG, after=datetime(2020, 7, 30, 12, 0, 0, 0, pytz.UTC)),
            [2, 3, 4, 5, 6],
        )
        assert_records(
            Archive.iter_all_records(self.org, Archive.TYPE_MSG, before=datetime(2020, 8, 2, 12, 0, 0, 0, pytz.UTC)),
            [1, 2, 3, 4, 5],
        )
        assert_records(
            Archive.iter_all_records(
                self.org,
                Archive.TYPE_MSG,
                after=datetime(2020, 7, 30, 12, 0, 0, 0, pytz.UTC),
                before=datetime(2020, 8, 2, 12, 0, 0, 0, pytz.UTC),
            ),
            [2, 3, 4, 5],
        )

    def test_end_date(self):
        daily = self.create_archive(Archive.TYPE_FLOWRUN, "D", date(2018, 2, 1), [], needs_deletion=True)
        monthly = self.create_archive(Archive.TYPE_FLOWRUN, "M", date(2018, 1, 1), [])

        self.assertEqual(date(2018, 2, 2), daily.get_end_date())
        self.assertEqual(date(2018, 2, 1), monthly.get_end_date())

        # check the start date of our db data
        self.assertEqual(date(2018, 2, 1), self.org.get_delete_date(archive_type=Archive.TYPE_FLOWRUN))


class ArchiveCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_empty_list(self):
        response = self.assertListFetch(
            reverse("archives.archive_run"), allow_viewers=False, allow_editors=True, context_objects=[]
        )

        self.assertContains(response, "Run Archive")

        response = self.assertListFetch(
            reverse("archives.archive_message"), allow_viewers=False, allow_editors=True, context_objects=[]
        )

        self.assertContains(response, "Message Archive")

    def test_archive_type_filter(self):
        # a daily archive that has been rolled up and will not appear in the results
        d1 = self.create_archive(Archive.TYPE_MSG, "D", date(2020, 7, 31), [{"id": 1}, {"id": 2}],)
        m1 = self.create_archive(Archive.TYPE_MSG, "M", date(2020, 7, 1), [{"id": 1}, {"id": 2}], rollup_of=(d1,),)
        d2 = self.create_archive(Archive.TYPE_MSG, "D", date(2020, 8, 1), [{"id": 3}, {"id": 4}],)
        d3 = self.create_archive(Archive.TYPE_FLOWRUN, "D", date(2020, 8, 1), [{"id": 3}, {"id": 4}],)

        # create archive for other org
        self.create_archive(Archive.TYPE_MSG, "D", date(2020, 7, 31), [{"id": 1}], org=self.org2)

        response = self.assertListFetch(
            reverse("archives.archive_run"), allow_viewers=False, allow_editors=True, context_objects=[d3]
        )
        self.assertContains(response, "jsonl.gz")

        response = self.assertListFetch(
            reverse("archives.archive_message"), allow_viewers=False, allow_editors=True, context_objects=[d2, m1]
        )
        self.assertContains(response, "jsonl.gz")

    def test_read(self):
        archive = self.create_archive(Archive.TYPE_MSG, "D", date(2020, 7, 31), [{"id": 1}, {"id": 2}])

        download_url = (
            f"https://s3-bucket.s3.amazonaws.com/things/{archive.hash}.jsonl.gz?response-content-disposition="
            f"attachment%3B&response-content-type=application%2Foctet&response-content-encoding=none"
        )

        response = self.assertReadFetch(
            reverse("archives.archive_read", args=[archive.id]), allow_viewers=False, allow_editors=True, status=302
        )

        self.assertIn(download_url, response.get("Location"))

    def test_formax(self):
        self.login(self.admin)
        url = reverse("orgs.org_home")

        response = self.client.get(url)
        self.assertContains(response, "archives yet")
        self.assertContains(response, reverse("archives.archive_message"))

        d1 = self.create_archive(Archive.TYPE_MSG, "D", date(2020, 7, 31), [{"id": 1}, {"id": 2}, {"id": 3}])
        self.create_archive(
            Archive.TYPE_MSG, "M", date(2020, 7, 1), [{"id": 1}, {"id": 2}, {"id": 3}], rollup_of=(d1,)
        )
        self.create_archive(Archive.TYPE_MSG, "D", date(2020, 8, 1), [{"id": 4}])

        response = self.client.get(url)
        self.assertContains(response, "4 records")
        self.assertContains(response, reverse("archives.archive_message"))
