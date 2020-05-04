from django.urls import reverse

from temba.tests import CRUDLTestMixin, TembaTest


class TicketerCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_connect(self):
        connect_url = reverse("tickets.ticketer_connect")

        self.assertListFetch(connect_url, allow_viewers=False, allow_editors=False)
