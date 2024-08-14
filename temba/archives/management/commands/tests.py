from datetime import date
from io import StringIO

from django.core.management import call_command

from temba.archives.models import Archive
from temba.tests import TembaTest


class SearchArchivesTest(TembaTest):
    def test_command(self):
        self.create_archive(
            Archive.TYPE_FLOWRUN,
            "D",
            date(2020, 8, 1),
            [{"id": 1, "created_on": "2020-07-30T10:00:00Z"}, {"id": 2, "created_on": "2020-07-30T15:00:00Z"}],
        )

        out = StringIO()
        call_command("search_archives", self.org.id, "run", where="", limit=10, stdout=out)

        self.assertIn('"id": 1', out.getvalue())
        self.assertIn("Fetched 2 records in", out.getvalue())
