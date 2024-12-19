from django.urls import reverse

from temba.ivr.models import Call
from temba.tests import CRUDLTestMixin, TembaTest


class CallCRUDLTest(CRUDLTestMixin, TembaTest):
    def test_list(self):
        list_url = reverse("ivr.call_list")

        contact = self.create_contact("Bob", phone="+123456789")

        call1 = Call.objects.create(
            org=self.org,
            channel=self.channel,
            direction=Call.DIRECTION_IN,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_COMPLETED,
            duration=15,
        )
        call2 = Call.objects.create(
            org=self.org,
            channel=self.channel,
            direction=Call.DIRECTION_OUT,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_IN_PROGRESS,
            duration=30,
        )
        Call.objects.create(
            org=self.org2,
            channel=self.channel,
            direction=Call.DIRECTION_IN,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_IN_PROGRESS,
            duration=15,
        )

        # check query count
        self.login(self.admin)
        with self.assertNumQueries(10):
            self.client.get(list_url)

        self.assertRequestDisallowed(list_url, [None, self.agent])
        self.assertListFetch(list_url, [self.user, self.editor, self.admin], context_objects=[call2, call1])
