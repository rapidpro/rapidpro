from datetime import date

from django.urls import reverse

from temba.archives.models import Archive
from temba.tests import CRUDLTestMixin, TembaTest


class ArchiveCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_list_views(self):
        # a daily archive that has been rolled up and will not appear in the results
        d1 = self.create_archive(Archive.TYPE_MSG, "D", date(2020, 7, 31), [{"id": 1}, {"id": 2}])
        m1 = self.create_archive(Archive.TYPE_MSG, "M", date(2020, 7, 1), [{"id": 1}, {"id": 2}], rollup_of=(d1,))
        d2 = self.create_archive(Archive.TYPE_MSG, "D", date(2020, 8, 1), [{"id": 3}, {"id": 4}])
        d3 = self.create_archive(Archive.TYPE_FLOWRUN, "D", date(2020, 8, 1), [{"id": 3}, {"id": 4}])

        # create archive for other org
        self.create_archive(Archive.TYPE_MSG, "D", date(2020, 7, 31), [{"id": 1}], org=self.org2)

        runs_url = reverse("archives.archive_run")
        msgs_url = reverse("archives.archive_message")

        self.assertRequestDisallowed(runs_url, [None, self.user, self.agent])
        self.assertRequestDisallowed(msgs_url, [None, self.user, self.agent])

        response = self.assertListFetch(runs_url, [self.editor, self.admin], context_objects=[d3])
        self.assertContains(response, f"/archive/read/{d3.id}/")

        response = self.assertListFetch(msgs_url, [self.editor, self.admin], context_objects=[d2, m1])
        self.assertContains(response, f"/archive/read/{d2.id}/")
        self.assertContains(response, f"/archive/read/{m1.id}/")

    def test_read(self):
        archive = self.create_archive(Archive.TYPE_MSG, "D", date(2020, 7, 31), [{"id": 1}, {"id": 2}])

        download_url = (
            f"http://localhost:9000/test-archives/{self.org.id}/message_D20200731_{archive.hash}.jsonl.gz?response-con"
            f"tent-disposition=attachment%3B&response-content-type=application%2Foctet&response-content-encoding=none"
        )

        self.assertRequestDisallowed(download_url, [None, self.user, self.agent, self.admin2])
        response = self.assertReadFetch(
            reverse("archives.archive_read", args=[archive.id]), [self.editor, self.admin], status=302
        )

        self.assertIn(download_url, response.get("Location"))
