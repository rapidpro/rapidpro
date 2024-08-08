from datetime import timedelta

from django_redis import get_redis_connection

from django.utils import timezone

from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import ChannelEvent
from temba.flows.models import FlowRun, FlowStart
from temba.ivr.models import Call
from temba.msgs.models import Msg
from temba.tests import TembaTest, matchers
from temba.tests.engine import MockSessionWriter
from temba.tickets.models import TicketEvent
from temba.utils import json

from . import queue_interrupt
from .events import Event


class MailroomQueueTest(TembaTest):
    def test_queue_flow_start(self):
        flow = self.get_flow("favorites")
        jim = self.create_contact("Jim", phone="+12065551212")
        bobs = self.create_group("Bobs", [self.create_contact("Bob", phone="+12065551313")])

        start = FlowStart.create(
            flow,
            self.admin,
            groups=[bobs],
            contacts=[jim],
            urns=["tel:+1234567890", "twitter:bobby"],
            params={"foo": "bar"},
        )

        start.async_start()

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "start_flow",
                "task": {
                    "start_id": start.id,
                    "start_type": "M",
                    "org_id": self.org.id,
                    "created_by_id": self.admin.id,
                    "flow_id": flow.id,
                    "contact_ids": [jim.id],
                    "group_ids": [bobs.id],
                    "urns": ["tel:+1234567890", "twitter:bobby"],
                    "query": None,
                    "exclusions": {},
                    "params": {"foo": "bar"},
                },
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_contact_import_batch(self):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        imp.start()

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "import_contact_batch",
                "task": {"contact_import_batch_id": imp.batches.get().id},
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_interrupt_channel(self):
        self.channel.release(self.admin)

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "interrupt_channel",
                "task": {"channel_id": self.channel.id},
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_interrupt_by_contacts(self):
        jim = self.create_contact("Jim", phone="+12065551212")
        bob = self.create_contact("Bob", phone="+12065551313")

        queue_interrupt(self.org, contacts=[jim, bob])

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {
                "type": "interrupt_sessions",
                "task": {"contact_ids": [jim.id, bob.id]},
                "queued_on": matchers.ISODate(),
            },
        )

    def test_queue_interrupt_by_flow(self):
        flow = self.get_flow("favorites")
        flow.archive(self.admin)

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {"type": "interrupt_sessions", "task": {"flow_ids": [flow.id]}, "queued_on": matchers.ISODate()},
        )

    def test_queue_interrupt_by_session(self):
        jim = self.create_contact("Jim", phone="+12065551212")

        flow = self.get_flow("favorites")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[2]

        (
            MockSessionWriter(jim, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        )

        run = FlowRun.objects.get(contact=jim)
        session = run.session
        run.delete()

        self.assert_org_queued(self.org, "batch")
        self.assert_queued_batch_task(
            self.org,
            {"type": "interrupt_sessions", "task": {"session_ids": [session.id]}, "queued_on": matchers.ISODate()},
        )

    def assert_org_queued(self, org, queue):
        r = get_redis_connection()

        # check we have one org with active tasks
        self.assertEqual(r.zcard(f"{queue}:active"), 1)

        queued_org = json.loads(r.zrange(f"{queue}:active", 0, 1)[0])

        self.assertEqual(queued_org, org.id)

    def assert_queued_batch_task(self, org, expected_task):
        r = get_redis_connection()

        # check we have one task in the org's queue
        self.assertEqual(r.zcard(f"batch:{org.id}"), 1)

        # load and check that task
        actual_task = json.loads(r.zrange(f"batch:{org.id}", 0, 1)[0])

        self.assertEqual(actual_task, expected_task)


class EventTest(TembaTest):
    def test_from_msg(self):
        contact1 = self.create_contact("Jim", phone="0979111111")
        contact2 = self.create_contact("Bob", phone="0979222222")

        # create msg that is too old to still have logs
        msg_in = self.create_incoming_msg(
            contact1,
            "Hello",
            external_id="12345",
            attachments=["image:http://a.jpg"],
            created_on=timezone.now() - timedelta(days=15),
        )

        self.assertEqual(
            {
                "type": "msg_received",
                "created_on": matchers.ISODate(),
                "msg": {
                    "uuid": str(msg_in.uuid),
                    "id": msg_in.id,
                    "urn": "tel:+250979111111",
                    "text": "Hello",
                    "attachments": ["image:http://a.jpg"],
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    "external_id": "12345",
                },
                "msg_type": "T",
                "visibility": "V",
                "logs_url": None,
            },
            Event.from_msg(self.org, self.admin, msg_in),
        )

        msg_in.visibility = Msg.VISIBILITY_DELETED_BY_USER
        msg_in.save(update_fields=("visibility",))

        self.assertEqual(
            {
                "type": "msg_received",
                "created_on": matchers.ISODate(),
                "msg": {
                    "uuid": str(msg_in.uuid),
                    "id": msg_in.id,
                    "urn": "tel:+250979111111",
                    "text": "",
                    "attachments": [],
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    "external_id": "12345",
                },
                "msg_type": "T",
                "visibility": "D",
                "logs_url": None,
            },
            Event.from_msg(self.org, self.admin, msg_in),
        )

        msg_in.visibility = Msg.VISIBILITY_DELETED_BY_SENDER
        msg_in.save(update_fields=("visibility",))

        self.assertEqual(
            {
                "type": "msg_received",
                "created_on": matchers.ISODate(),
                "msg": {
                    "uuid": str(msg_in.uuid),
                    "id": msg_in.id,
                    "urn": "tel:+250979111111",
                    "text": "",
                    "attachments": [],
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    "external_id": "12345",
                },
                "msg_type": "T",
                "visibility": "X",
                "logs_url": None,
            },
            Event.from_msg(self.org, self.admin, msg_in),
        )

        msg_out = self.create_outgoing_msg(
            contact1, "Hello", channel=self.channel, status="E", quick_replies=["yes", "no"], created_by=self.agent
        )

        self.assertEqual(
            {
                "type": "msg_created",
                "created_on": matchers.ISODate(),
                "msg": {
                    "uuid": str(msg_out.uuid),
                    "id": msg_out.id,
                    "urn": "tel:+250979111111",
                    "text": "Hello",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    "quick_replies": ["yes", "no"],
                },
                "created_by": {
                    "id": self.agent.id,
                    "email": "agent@nyaruka.com",
                    "first_name": "Agnes",
                    "last_name": "",
                },
                "optin": None,
                "status": "E",
                "logs_url": f"/channels/{str(self.channel.uuid)}/logs/msg/{msg_out.id}/",
            },
            Event.from_msg(self.org, self.admin, msg_out),
        )

        msg_out = self.create_outgoing_msg(contact1, "Hello", status="F", failed_reason=Msg.FAILED_NO_DESTINATION)

        self.assertEqual(
            {
                "type": "msg_created",
                "created_on": matchers.ISODate(),
                "msg": {
                    "uuid": str(msg_out.uuid),
                    "id": msg_out.id,
                    "urn": None,
                    "text": "Hello",
                    "channel": None,
                },
                "created_by": None,
                "optin": None,
                "status": "F",
                "failed_reason": "D",
                "failed_reason_display": "No suitable channel found",
                "logs_url": None,
            },
            Event.from_msg(self.org, self.admin, msg_out),
        )

        ivr_out = self.create_outgoing_msg(contact1, "Hello", voice=True)

        self.assertEqual(
            {
                "type": "ivr_created",
                "created_on": matchers.ISODate(),
                "msg": {
                    "uuid": str(ivr_out.uuid),
                    "id": ivr_out.id,
                    "urn": "tel:+250979111111",
                    "text": "Hello",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                },
                "created_by": None,
                "status": "S",
                "logs_url": f"/channels/{str(self.channel.uuid)}/logs/msg/{ivr_out.id}/",
            },
            Event.from_msg(self.org, self.admin, ivr_out),
        )

        bcast = self.create_broadcast(self.admin, {"und": {"text": "Hi there"}}, contacts=[contact1, contact2])
        msg_out2 = bcast.msgs.filter(contact=contact1).get()

        self.assertEqual(
            {
                "type": "broadcast_created",
                "created_on": matchers.ISODate(),
                "translations": {"und": {"text": "Hi there"}},
                "base_language": "und",
                "msg": {
                    "uuid": str(msg_out2.uuid),
                    "id": msg_out2.id,
                    "urn": "tel:+250979111111",
                    "text": "Hi there",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                },
                "created_by": {
                    "id": self.admin.id,
                    "email": "admin@nyaruka.com",
                    "first_name": "Andy",
                    "last_name": "",
                },
                "optin": None,
                "status": "S",
                "recipient_count": 2,
                "logs_url": f"/channels/{str(self.channel.uuid)}/logs/msg/{msg_out2.id}/",
            },
            Event.from_msg(self.org, self.admin, msg_out2),
        )

        # create a broadcast that was sent with an opt-in
        optin = self.create_optin("Polls")
        bcast2 = self.create_broadcast(
            self.admin, {"und": {"text": "Hi there"}}, contacts=[contact1, contact2], optin=optin
        )
        msg_out3 = bcast2.msgs.filter(contact=contact1).get()

        self.assertEqual(
            {
                "type": "broadcast_created",
                "created_on": matchers.ISODate(),
                "translations": {"und": {"text": "Hi there"}},
                "base_language": "und",
                "msg": {
                    "uuid": str(msg_out3.uuid),
                    "id": msg_out3.id,
                    "urn": "tel:+250979111111",
                    "text": "Hi there",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                },
                "created_by": {
                    "id": self.admin.id,
                    "email": "admin@nyaruka.com",
                    "first_name": "Andy",
                    "last_name": "",
                },
                "optin": {"uuid": str(optin.uuid), "name": "Polls"},
                "status": "S",
                "recipient_count": 2,
                "logs_url": f"/channels/{str(self.channel.uuid)}/logs/msg/{msg_out3.id}/",
            },
            Event.from_msg(self.org, self.admin, msg_out3),
        )

        # create a message that was an opt-in request
        msg_out4 = self.create_optin_request(contact1, self.channel, optin)
        self.assertEqual(
            {
                "type": "optin_requested",
                "created_on": matchers.ISODate(),
                "optin": {"uuid": str(optin.uuid), "name": "Polls"},
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                "urn": "tel:+250979111111",
                "created_by": None,
                "status": "S",
                "logs_url": f"/channels/{str(self.channel.uuid)}/logs/msg/{msg_out4.id}/",
            },
            Event.from_msg(self.org, self.admin, msg_out4),
        )

    def test_from_channel_event(self):
        self.create_contact("Jim", phone="+250979111111")

        event1 = self.create_channel_event(
            self.channel, "tel:+250979111111", ChannelEvent.TYPE_CALL_IN, extra={"duration": 5}
        )

        self.assertEqual(
            {
                "type": "channel_event",
                "created_on": matchers.ISODate(),
                "event": {
                    "type": "mo_call",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    "duration": 5,
                },
                "channel_event_type": "mo_call",  # deprecated
                "duration": 5,  # deprecated
            },
            Event.from_channel_event(self.org, self.admin, event1),
        )

        optin = self.create_optin("Polls")
        event2 = self.create_channel_event(
            self.channel,
            "tel:+250979111111",
            ChannelEvent.TYPE_OPTIN,
            optin=optin,
            extra={"title": "Polls", "payload": str(optin.id)},
        )

        self.assertEqual(
            {
                "type": "channel_event",
                "created_on": matchers.ISODate(),
                "event": {
                    "type": "optin",
                    "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    "optin": {"uuid": str(optin.uuid), "name": "Polls"},
                },
                "channel_event_type": "optin",  # deprecated
                "duration": None,  # deprecated
            },
            Event.from_channel_event(self.org, self.admin, event2),
        )

    def test_from_flow_run(self):
        contact = self.create_contact("Jim", phone="0979111111")
        flow = self.get_flow("color_v13")
        nodes = flow.get_definition()["nodes"]
        (
            MockSessionWriter(contact, flow)
            .visit(nodes[0])
            .send_msg("What is your favorite color?", self.channel)
            .wait()
            .save()
        )
        run = contact.runs.get()

        self.assertEqual(
            {
                "type": "flow_entered",
                "created_on": matchers.ISODate(),
                "flow": {"uuid": str(flow.uuid), "name": "Colors"},
                "logs_url": None,
            },
            Event.from_flow_run(self.org, self.admin, run),
        )

        # customer support get access to logs
        self.assertEqual(
            {
                "type": "flow_entered",
                "created_on": matchers.ISODate(),
                "flow": {"uuid": str(flow.uuid), "name": "Colors"},
                "logs_url": f"/flowsession/json/{run.session.uuid}/",
            },
            Event.from_flow_run(self.org, self.customer_support, run),
        )

    def test_from_event_fire(self):
        flow = self.get_flow("color_v13")
        group = self.create_group("Reporters", contacts=[])
        registered = self.create_field("registered", "Registered", value_type="D")
        campaign = Campaign.create(self.org, self.admin, "Welcomes", group)
        event = CampaignEvent.create_flow_event(
            self.org, self.user, campaign, registered, offset=1, unit="W", flow=flow
        )
        contact = self.create_contact("Jim", phone="0979111111")
        fire = EventFire.objects.create(
            event=event,
            contact=contact,
            scheduled=timezone.now(),
            fired=timezone.now(),
            fired_result=EventFire.RESULT_FIRED,
        )

        self.assertEqual(
            {
                "type": "campaign_fired",
                "created_on": fire.fired.isoformat(),
                "campaign": {"id": campaign.id, "name": "Welcomes", "uuid": campaign.uuid},
                "campaign_event": {
                    "id": event.id,
                    "offset_display": "1 week after",
                    "relative_to": {"key": "registered", "name": "Registered"},
                },
                "fired_result": "F",
            },
            Event.from_event_fire(self.org, self.admin, fire),
        )

    def test_from_ticket_event(self):
        contact = self.create_contact("Jim", phone="0979111111")
        ticket = self.create_ticket(contact)

        # event with a user
        event1 = TicketEvent.objects.create(
            org=self.org,
            contact=contact,
            ticket=ticket,
            event_type=TicketEvent.TYPE_NOTE_ADDED,
            created_by=self.agent,
            note="this is important",
        )

        self.assertEqual(
            {
                "type": "ticket_note_added",
                "note": "this is important",
                "topic": None,
                "assignee": None,
                "ticket": {
                    "uuid": str(ticket.uuid),
                    "opened_on": matchers.ISODate(),
                    "closed_on": None,
                    "status": "O",
                    "topic": {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"},
                },
                "created_on": matchers.ISODate(),
                "created_by": {
                    "id": self.agent.id,
                    "first_name": "Agnes",
                    "last_name": "",
                    "email": "agent@nyaruka.com",
                },
            },
            Event.from_ticket_event(self.org, self.user, event1),
        )

        # event without a user
        event2 = TicketEvent.objects.create(
            org=self.org, contact=contact, ticket=ticket, event_type=TicketEvent.TYPE_CLOSED
        )

        self.assertEqual(
            {
                "type": "ticket_closed",
                "note": None,
                "topic": None,
                "assignee": None,
                "ticket": {
                    "uuid": str(ticket.uuid),
                    "opened_on": matchers.ISODate(),
                    "closed_on": None,
                    "status": "O",
                    "topic": {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"},
                },
                "created_on": matchers.ISODate(),
                "created_by": None,
            },
            Event.from_ticket_event(self.org, self.user, event2),
        )

    def test_from_ivr_call(self):
        flow = self.create_flow("IVR", flow_type="V")
        contact = self.create_contact("Jim", phone="0979111111")

        # create call that is too old to still have logs
        call1 = self.create_incoming_call(
            flow, contact, status=Call.STATUS_IN_PROGRESS, created_on=timezone.now() - timedelta(days=15)
        )

        # and one that will have logs
        call2 = self.create_incoming_call(flow, contact, status=Call.STATUS_ERRORED, error_reason=Call.ERROR_BUSY)

        self.assertEqual(
            {
                "type": "call_started",
                "status": "I",
                "status_display": "In Progress",
                "created_on": matchers.ISODate(),
                "logs_url": None,
            },
            Event.from_ivr_call(self.org, self.admin, call1),
        )

        self.assertEqual(
            {
                "type": "call_started",
                "status": "E",
                "status_display": "Errored (Busy)",
                "created_on": matchers.ISODate(),
                "logs_url": None,  # user can't see logs
            },
            Event.from_ivr_call(self.org, self.user, call2),
        )
        self.assertEqual(
            {
                "type": "call_started",
                "status": "E",
                "status_display": "Errored (Busy)",
                "created_on": matchers.ISODate(),
                "logs_url": f"/channels/{call2.channel.uuid}/logs/call/{call2.id}/",
            },
            Event.from_ivr_call(self.org, self.admin, call2),
        )
