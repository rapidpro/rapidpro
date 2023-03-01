from django.urls import reverse

from temba.channels.models import Channel
from temba.contacts.models import ContactURN
from temba.msgs.models import Msg
from temba.tests import TembaTest
from temba.utils import json

from .sync import get_sync_commands


class AndroidTest(TembaTest):
    def test_register_unsupported_android(self):
        # remove our explicit country so it needs to be derived from channels
        self.org.country = None
        self.org.save()

        Channel.objects.all().delete()

        reg_data = dict(cmds=[dict(cmd="gcm", gcm_id="GCM111", uuid="uuid"), dict(cmd="status", cc="RW", dev="Nexus")])

        # try a post register
        response = self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")
        self.assertEqual(200, response.status_code)

        response_json = response.json()
        self.assertEqual(
            response_json,
            dict(cmds=[dict(cmd="reg", relayer_claim_code="*********", relayer_secret="0" * 64, relayer_id=-1)]),
        )

        # missing uuid raises
        reg_data = dict(cmds=[dict(cmd="fcm", fcm_id="FCM111"), dict(cmd="status", cc="RW", dev="Nexus")])

        with self.assertRaises(ValueError):
            self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")

        # missing fcm_id raises
        reg_data = dict(cmds=[dict(cmd="fcm", uuid="uuid"), dict(cmd="status", cc="RW", dev="Nexus")])
        with self.assertRaises(ValueError):
            self.client.post(reverse("register"), json.dumps(reg_data), content_type="application/json")

    def test_get_sync_commands(self):
        joe = self.create_contact("Joe Blow", phone="123")
        ContactURN.create(self.org, joe, "tel:789")

        frank = self.create_contact("Frank Blow", phone="321")
        kevin = self.create_contact("Kevin Durant", phone="987")

        msg1 = self.create_outgoing_msg(joe, "Hello, we heard from you.")
        msg2 = self.create_outgoing_msg(frank, "Hello, we heard from you.")
        msg3 = self.create_outgoing_msg(kevin, "Hello, we heard from you.")

        commands = get_sync_commands(Msg.objects.filter(id__in=(msg1.id, msg2.id, msg3.id)))

        self.assertEqual(
            commands,
            [
                {
                    "cmd": "mt_bcast",
                    "to": [
                        {"phone": "123", "id": msg1.id},
                        {"phone": "321", "id": msg2.id},
                        {"phone": "987", "id": msg3.id},
                    ],
                    "msg": "Hello, we heard from you.",
                }
            ],
        )

        msg4 = self.create_outgoing_msg(kevin, "Hello, there")

        commands = get_sync_commands(Msg.objects.filter(id__in=(msg1.id, msg2.id, msg4.id)))

        self.assertEqual(
            commands,
            [
                {
                    "cmd": "mt_bcast",
                    "to": [{"phone": "123", "id": msg1.id}, {"phone": "321", "id": msg2.id}],
                    "msg": "Hello, we heard from you.",
                },
                {"cmd": "mt_bcast", "to": [{"phone": "987", "id": msg4.id}], "msg": "Hello, there"},
            ],
        )

        msg5 = self.create_outgoing_msg(frank, "Hello, we heard from you.")

        commands = get_sync_commands(Msg.objects.filter(id__in=(msg1.id, msg4.id, msg5.id)))

        self.assertEqual(
            commands,
            [
                {"cmd": "mt_bcast", "to": [{"phone": "123", "id": msg1.id}], "msg": "Hello, we heard from you."},
                {"cmd": "mt_bcast", "to": [{"phone": "987", "id": msg4.id}], "msg": "Hello, there"},
                {"cmd": "mt_bcast", "to": [{"phone": "321", "id": msg5.id}], "msg": "Hello, we heard from you."},
            ],
        )
