from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command

from temba.archives.models import Archive
from temba.tests import TembaTest
from temba.tests.s3 import MockS3Client


class SearchArchivesTest(TembaTest):
    @patch("temba.archives.models.Archive.s3_client")
    def test_command(self, mock_s3_client):
        mock_s3 = MockS3Client()
        mock_s3_client.return_value = mock_s3

        self.create_archive(
            Archive.TYPE_FLOWRUN,
            "D",
            date(2020, 8, 1),
            [{"id": 1, "created_on": "2020-07-30T10:00:00Z"}, {"id": 2, "created_on": "2020-07-30T15:00:00Z"}],
            s3=mock_s3,
        )

        out = StringIO()
        call_command("search_archives", self.org.id, "run", expression="", limit=10, stdout=out)

        self.assertIn('"id": 1', out.getvalue())
        self.assertIn("Fetched 2 records in", out.getvalue())
