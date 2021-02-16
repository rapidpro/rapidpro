from collections import defaultdict

from django.db.models import Manager
from django.utils.timezone import now

from temba.flows.models import FlowRun
from temba.msgs.models import Msg


class MessagesReportManager(Manager):
    def count_messages_for_date(self, org, date=None):
        if date is None:
            date = now().date()

        message_groups = defaultdict(list)

        # calculate massages that not belong to flows
        message_groups[None] = (
            Msg.objects.filter(org=org, queued_on__startswith=date).exclude(msg_type='F').values_list("uuid", flat=True)
        )

        # calculate messages that belong to flows
        runs = FlowRun.objects.filter(
            org=org,
            status__in=[
                FlowRun.STATUS_COMPLETED, FlowRun.STATUS_INTERRUPTED, FlowRun.STATUS_FAILED, FlowRun.STATUS_EXPIRED
            ],
            exited_on__startswith=date
        )
        for run in runs:
            for e in run.get_msg_events():
                msg_uuid = e["msg"].get("uuid")
                if msg_uuid and msg_uuid != "None":
                    message_groups[run.flow.uuid].append(msg_uuid)

        data = {}
        for flow_uuid, message_uuids in message_groups.items():
            Msg.objects.filter(uuid__in=message_uuids).values()
