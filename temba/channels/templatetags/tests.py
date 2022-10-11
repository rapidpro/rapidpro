from datetime import timedelta

from django.template import Context
from django.utils import timezone

from temba.tests import TembaTest

from .channels import channel_log_link


class ChannelsTest(TembaTest):
    def test_channel_log_link(self):
        flow = self.create_flow("IVR")
        joe = self.create_contact("Joe", phone="+1234567890")
        msg = self.create_incoming_msg(joe, "Hi")
        call = self.create_incoming_call(flow, joe)
        old_msg = self.create_incoming_msg(joe, "Hi", created_on=timezone.now() - timedelta(days=7))
        surveyor_msg = self.create_incoming_msg(joe, "Submitted", surveyor=True)

        call_logs_url = f"/channels/{self.channel.uuid}/logs/call/{call.id}/"
        msg_logs_url = f"/channels/{self.channel.uuid}/logs/msg/{msg.id}/"

        # admin user sees links to msg and call logs
        self.assertEqual(
            {"logs_url": call_logs_url}, channel_log_link(Context({"user_org": self.org, "user": self.admin}), call)
        )
        self.assertEqual(
            {"logs_url": msg_logs_url}, channel_log_link(Context({"user_org": self.org, "user": self.admin}), msg)
        )

        # editor user doesn't
        self.assertEqual(
            {"logs_url": None}, channel_log_link(Context({"user_org": self.org, "user": self.editor}), call)
        )
        self.assertEqual(
            {"logs_url": None}, channel_log_link(Context({"user_org": self.org, "user": self.editor}), msg)
        )

        # no log link for channel-less messages or older messages
        self.assertEqual(
            {"logs_url": None}, channel_log_link(Context({"user_org": self.org, "user": self.admin}), surveyor_msg)
        )
        self.assertEqual(
            {"logs_url": None}, channel_log_link(Context({"user_org": self.org, "user": self.admin}), old_msg)
        )
