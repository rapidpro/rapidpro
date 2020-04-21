from datetime import date
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from temba.tests import CRUDLTestMixin, TembaTest
from temba.tests.s3 import MockS3Client
from temba.utils.uuid import uuid4

from .models import Archive


class ArchiveTest(TembaTest):
    def test_iter_records(self):
        archive = Archive.objects.create(
            org=self.org,
            archive_type=Archive.TYPE_FLOWRUN,
            size=10,
            hash=uuid4().hex,
            url=f"http://s3-bucket.aws.com/my/32562662.jsonl.gz",
            record_count=2,
            start_date=timezone.now(),
            period="D",
            build_time=23425,
        )

        mock_s3 = MockS3Client()
        mock_s3.put_jsonl("s3-bucket", "my/32562662.jsonl.gz", [{"id": 1}, {"id": 2}, {"id": 3}])

        with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
            records_iter = archive.iter_records()

            self.assertEqual(next(records_iter), {"id": 1})
            self.assertEqual(next(records_iter), {"id": 2})
            self.assertEqual(next(records_iter), {"id": 3})
            self.assertRaises(StopIteration, next, records_iter)

    def test_end_date(self):

        daily = Archive.objects.create(
            org=self.org,
            archive_type=Archive.TYPE_FLOWRUN,
            size=10,
            hash=uuid4().hex,
            url=f"http://s3-bucket.aws.com/my/32562662.jsonl.gz",
            record_count=100,
            start_date=date(2018, 2, 1),
            period="D",
            build_time=1234,
            needs_deletion=True,
        )

        monthly = Archive.objects.create(
            org=self.org,
            archive_type=Archive.TYPE_FLOWRUN,
            size=10,
            hash=uuid4().hex,
            url=f"http://s3-bucket.aws.com/my/32562663.jsonl.gz",
            record_count=2000,
            start_date=date(2018, 1, 1),
            period="M",
            build_time=1234,
            needs_deletion=False,
        )

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
