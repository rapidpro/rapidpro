from datetime import date, datetime
from unittest.mock import patch

import pytz

from django.urls import reverse
from django.utils import timezone

from temba.tests import CRUDLTestMixin, TembaTest
from temba.tests.s3 import MockS3Client
from temba.utils.text import random_string
from temba.utils.uuid import uuid4

from .models import Archive


class ArchiveTest(TembaTest):
    def create_archive(self, s3, archive_type, period, start_date, records=(), needs_deletion=False, rollup_of=()):
        bucket = "s3-bucket"
        key = f"things/{random_string(10)}.jsonl.gz"
        s3.put_jsonl(bucket, key, records)
        archive = Archive.objects.create(
            org=self.org,
            archive_type=archive_type,
            size=10,
            hash=uuid4().hex,
            url=f"http://{bucket}.aws.com/{key}",
            record_count=len(records),
            start_date=start_date,
            period=period,
            build_time=23425,
            needs_deletion=needs_deletion,
        )
        if rollup_of:
            Archive.objects.filter(id__in=[a.id for a in rollup_of]).update(rollup=archive)
        return archive

    def test_iter_records(self):
        mock_s3 = MockS3Client()
        archive = self.create_archive(
            mock_s3, Archive.TYPE_MSG, "D", timezone.now().date(), [{"id": 1}, {"id": 2}, {"id": 3}]
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
            mock_s3, Archive.TYPE_MSG, "D", timezone.now().date(), [{"id": 1}, {"id": 2}, {"id": 3}]
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
            mock_s3,
            Archive.TYPE_MSG,
            "D",
            date(2020, 7, 31),
            [{"id": 1, "created_on": "2020-07-30T10:00:00Z"}, {"id": 2, "created_on": "2020-07-30T15:00:00Z"}],
        )
        self.create_archive(
            mock_s3,
            Archive.TYPE_MSG,
            "M",
            date(2020, 7, 1),
            [{"id": 1, "created_on": "2020-07-30T10:00:00Z"}, {"id": 2, "created_on": "2020-07-30T15:00:00Z"}],
            rollup_of=(d1,),
        )
        self.create_archive(
            mock_s3,
            Archive.TYPE_MSG,
            "D",
            date(2020, 8, 1),
            [{"id": 3, "created_on": "2020-08-01T10:00:00Z"}, {"id": 4, "created_on": "2020-08-01T15:00:00Z"}],
        )
        self.create_archive(
            mock_s3,
            Archive.TYPE_FLOWRUN,
            "D",
            date(2020, 8, 1),
            [{"id": 3, "created_on": "2020-08-01T10:00:00Z"}, {"id": 4, "created_on": "2020-08-01T15:00:00Z"}],
        )
        self.create_archive(
            mock_s3,
            Archive.TYPE_MSG,
            "D",
            date(2020, 8, 2),
            [{"id": 5, "created_on": "2020-08-02T10:00:00Z"}, {"id": 6, "created_on": "2020-08-02T15:00:00Z"}],
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
        mock_s3 = MockS3Client()
        daily = self.create_archive(mock_s3, Archive.TYPE_FLOWRUN, "D", date(2018, 2, 1), [], needs_deletion=True)
        monthly = self.create_archive(mock_s3, Archive.TYPE_FLOWRUN, "M", date(2018, 1, 1), [])

        self.assertEqual(date(2018, 2, 2), daily.get_end_date())
        self.assertEqual(date(2018, 2, 1), monthly.get_end_date())

        # check the start date of our db data
        self.assertEqual(date(2018, 2, 1), self.org.get_delete_date(archive_type=Archive.TYPE_FLOWRUN))


class ArchiveCRUDLTest(TembaTest, CRUDLTestMixin):
    def create_archive(self, org, idx, start_date=None, period="D"):

        if not start_date:
            start_date = date(2018, idx, 1)
            period = "M"

        archive_hash = uuid4().hex
        return Archive.objects.create(
            archive_type=Archive.TYPE_MSG if idx % 2 == 0 else Archive.TYPE_FLOWRUN,
            size=100_000 * idx,
            hash=archive_hash,
            url=f"http://s3-bucket.aws.com/my/{archive_hash}.jsonl.gz",
            record_count=123_456_789 * idx,
            start_date=start_date,
            period=period,
            build_time=idx * 123,
            org=org,
        )

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
        archives = [self.create_archive(self.org, idx) for idx in range(1, 10)]

        # create a daily archive
        self.create_archive(self.org, 1, start_date=date(2018, 2, 1), period="D")

        # create a daily archive that has been rolled up and will not appear in the results
        Archive.objects.create(
            org=self.org,
            start_date=date(2018, 10, 5),
            build_time=12,
            record_count=34,
            size=345,
            hash="feca9988b7772c003204a28bd741d0d0",
            archive_type=Archive.TYPE_FLOWRUN,
            period=Archive.PERIOD_DAILY,
            rollup_id=archives[-1].id,
        )

        # create archive for other org
        self.create_archive(self.org2, 1)

        response = self.assertListFetch(
            reverse("archives.archive_run"), allow_viewers=False, allow_editors=True, context_object_count=6
        )
        self.assertContains(response, "jsonl.gz")

        response = self.assertListFetch(
            reverse("archives.archive_message"), allow_viewers=False, allow_editors=True, context_object_count=4
        )
        self.assertContains(response, "jsonl.gz")

    def test_read(self):
        archive = self.create_archive(self.org, 1)

        download_url = (
            f"https://s3-bucket.s3.amazonaws.com/my/{archive.hash}.jsonl.gz?response-content-disposition="
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

        self.create_archive(self.org, 1)

        response = self.client.get(url)
        self.assertContains(response, "123,456,789 records")
        self.assertContains(response, reverse("archives.archive_message"))
