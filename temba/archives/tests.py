from datetime import date
from uuid import uuid4

from mock import patch

from django.core.urlresolvers import reverse
from django.utils import timezone

from temba.tests import TembaTest
from temba.tests.s3 import MockS3Client

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


class ArchiveViewTest(TembaTest):
    def create_archive(self, idx, start_date=None, period="D"):

        if not start_date:
            start_date = date(2018, idx, 1)
            period = "M"

        archive_hash = uuid4().hex
        return Archive.objects.create(
            archive_type=Archive.TYPE_MSG if idx % 2 == 0 else Archive.TYPE_FLOWRUN,
            size=100000 * idx,
            hash=archive_hash,
            url=f"http://s3-bucket.aws.com/my/{archive_hash}.jsonl.gz",
            record_count=123456789 * idx,
            start_date=start_date,
            period=period,
            build_time=idx * 123,
            org=self.org,
        )

    def test_empty_list(self):
        self.login(self.admin)
        response = self.client.get(reverse("archives.archive_list", args=["run"]))
        self.assertEqual(0, response.context["object_list"].count())
        self.assertEqual("Run Archive", response.context["title"])

        response = self.client.get(reverse("archives.archive_list", args=["message"]))
        self.assertEqual(0, response.context["object_list"].count())
        self.assertEqual("Message Archive", response.context["title"])

    def test_archive_type_filter(self):
        archives = [self.create_archive(idx) for idx in range(1, 10)]

        # create a daily archive
        self.create_archive(1, start_date=date(2018, 2, 1), period="D")

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

        self.login(self.admin)

        # make sure we have the right number of each
        response = self.client.get(reverse("archives.archive_list", args=["run"]))
        self.assertEqual(6, response.context["object_list"].count())
        self.assertContains(response, "jsonl.gz")

        response = self.client.get(reverse("archives.archive_list", args=["message"]))
        self.assertEqual(4, response.context["object_list"].count())
        self.assertContains(response, "jsonl.gz")

    def test_download(self):
        self.login(self.admin)

        archive = self.create_archive(1)
        response = self.client.get(reverse("archives.archive_read", args=[archive.id]))
        url = response.get("Location")

        self.assertEqual(302, response.status_code)
        self.assertIn(
            f"https://s3-bucket.s3.amazonaws.com/my/{archive.hash}.jsonl.gz?"
            f"response-content-disposition=attachment%3B&"
            f"response-content-type=application%2Foctet&"
            f"response-content-encoding=none",
            url,
        )
