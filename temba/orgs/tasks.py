import logging
from datetime import timedelta

from celery import shared_task

from django.utils import timezone

from temba.contacts.models import URN, ContactURN, ExportContactsTask
from temba.contacts.tasks import export_contacts_task
from temba.flows.models import ExportFlowResultsTask
from temba.flows.tasks import export_flow_results_task
from temba.msgs.models import ExportMessagesTask
from temba.msgs.tasks import export_messages_task
from temba.utils.crons import cron_task

from .models import Invitation, Org


@shared_task
def send_invitation_email_task(invitation_id):
    invitation = Invitation.objects.get(pk=invitation_id)
    invitation.send_email()


@shared_task
def normalize_contact_tels_task(org_id):
    org = Org.objects.get(id=org_id)

    # do we have an org-level country code? if so, try to normalize any numbers not starting with +
    if org.default_country_code:
        urns = ContactURN.objects.filter(org=org, scheme=URN.TEL_SCHEME).exclude(path__startswith="+").iterator()
        for urn in urns:
            urn.ensure_number_normalization(org.default_country_code)


@cron_task(lock_timeout=7200)
def resume_failed_tasks():
    now = timezone.now()
    window = now - timedelta(hours=1)

    contact_exports = ExportContactsTask.objects.filter(modified_on__lte=window).exclude(
        status__in=[ExportContactsTask.STATUS_COMPLETE, ExportContactsTask.STATUS_FAILED]
    )
    for contact_export in contact_exports:
        export_contacts_task.delay(contact_export.pk)

    flow_results_exports = ExportFlowResultsTask.objects.filter(modified_on__lte=window).exclude(
        status__in=[ExportFlowResultsTask.STATUS_COMPLETE, ExportFlowResultsTask.STATUS_FAILED]
    )
    for flow_results_export in flow_results_exports:
        export_flow_results_task.delay(flow_results_export.pk)

    msg_exports = ExportMessagesTask.objects.filter(modified_on__lte=window).exclude(
        status__in=[ExportMessagesTask.STATUS_COMPLETE, ExportMessagesTask.STATUS_FAILED]
    )
    for msg_export in msg_exports:
        export_messages_task.delay(msg_export.pk)


@cron_task(lock_timeout=7200)
def delete_released_orgs():
    # for each org that was released over 7 days ago, delete it for real
    week_ago = timezone.now() - timedelta(days=Org.DELETE_DELAY_DAYS)
    for org in Org.objects.filter(is_active=False, released_on__lt=week_ago, deleted_on=None):
        try:
            org.delete()
        except Exception:  # pragma: no cover
            logging.exception(f"exception while deleting {org.name}")
