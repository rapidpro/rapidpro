import logging
from datetime import timedelta

from celery import shared_task

from django.db.models import Prefetch
from django.utils import timezone

from temba.contacts.models import ContactField, ContactGroup
from temba.utils import analytics
from temba.utils.crons import cron_task

from .models import Broadcast, BroadcastMsgCount, ExportMessagesTask, LabelCount, Media, Msg, SystemLabelCount

logger = logging.getLogger(__name__)


@shared_task
def send_to_flow_node(org_id, user_id, text: str, **kwargs):
    from django.contrib.auth.models import User

    from temba.contacts.models import Contact
    from temba.flows.models import FlowRun
    from temba.orgs.models import Org

    org = Org.objects.get(pk=org_id)
    user = User.objects.get(pk=user_id)
    node_uuid = kwargs.get("s", None)

    runs = FlowRun.objects.filter(
        org=org, current_node_uuid=node_uuid, status__in=(FlowRun.STATUS_ACTIVE, FlowRun.STATUS_WAITING)
    )

    contact_ids = list(
        Contact.objects.filter(org=org, status=Contact.STATUS_ACTIVE, is_active=True)
        .filter(id__in=runs.values_list("contact", flat=True))
        .values_list("id", flat=True)
    )

    if contact_ids:
        broadcast = Broadcast.create(org, user, {"und": text}, contact_ids=contact_ids)
        broadcast.send_async()

        analytics.track(user, "temba.broadcast_created", dict(contacts=len(contact_ids), groups=0, urns=0))


@cron_task()
def fail_old_messages():
    """
    Looks for any stalled outgoing messages older than 1 week. These are typically from Android relayers which have
    stopped syncing, and would be confusing to go out.
    """
    one_week_ago = timezone.now() - timedelta(days=7)
    too_old = Msg.objects.filter(
        created_on__lte=one_week_ago,
        direction=Msg.DIRECTION_OUT,
        status__in=(Msg.STATUS_INITIALIZING, Msg.STATUS_QUEUED, Msg.STATUS_ERRORED),
    )
    num_failed = too_old.update(status=Msg.STATUS_FAILED, failed_reason=Msg.FAILED_TOO_OLD, modified_on=timezone.now())

    return {"failed": num_failed}


@shared_task
def export_messages_task(export_id):
    """
    Export messages to a file and e-mail a link to the user
    """
    ExportMessagesTask.objects.select_related("org", "created_by").prefetch_related(
        Prefetch("with_fields", ContactField.objects.order_by("name")),
        Prefetch("with_groups", ContactGroup.objects.order_by("name")),
    ).get(id=export_id).perform()


@cron_task(lock_timeout=7200)
def squash_msg_counts():
    SystemLabelCount.squash()
    LabelCount.squash()
    BroadcastMsgCount.squash()


@shared_task
def process_media_upload(media_id):
    Media.objects.get(id=media_id).process_upload()
