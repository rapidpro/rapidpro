import logging

from django.db.models import Prefetch

from celery import shared_task

from temba.contacts.models import ContactField, ContactGroup
from temba.utils import analytics
from temba.utils.celery import nonoverlapping_task

from .models import Broadcast, BroadcastMsgCount, ExportMessagesTask, LabelCount, Media, Msg, SystemLabelCount

logger = logging.getLogger(__name__)


@shared_task(track_started=True, name="send_to_flow_node")
def send_to_flow_node(org_id, user_id, text, **kwargs):
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
        broadcast = Broadcast.create(org, user, text, contact_ids=contact_ids)
        broadcast.send_async()

        analytics.track(user, "temba.broadcast_created", dict(contacts=len(contact_ids), groups=0, urns=0))


@shared_task(track_started=True, name="fail_old_messages")
def fail_old_messages():  # pragma: needs cover
    Msg.fail_old_messages()


@shared_task(track_started=True, name="export_sms_task")
def export_messages_task(export_id):
    """
    Export messages to a file and e-mail a link to the user
    """
    ExportMessagesTask.objects.select_related("org", "created_by").prefetch_related(
        Prefetch("with_fields", ContactField.objects.order_by("name")),
        Prefetch("with_groups", ContactGroup.objects.order_by("name")),
    ).get(id=export_id).perform()


@nonoverlapping_task(track_started=True, name="squash_msgcounts", lock_timeout=7200)
def squash_msgcounts():
    SystemLabelCount.squash()
    LabelCount.squash()
    BroadcastMsgCount.squash()


@shared_task
def process_media_upload(media_id):
    Media.objects.get(id=media_id).process_upload()
