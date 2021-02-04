from datetime import timedelta

from django.utils import timezone

from celery.task import task

from temba.contacts.models import URN, ContactURN, ExportContactsTask
from temba.contacts.tasks import export_contacts_task
from temba.flows.models import ExportFlowResultsTask
from temba.flows.tasks import export_flow_results_task
from temba.msgs.models import ExportMessagesTask
from temba.msgs.tasks import export_messages_task
from temba.utils.celery import nonoverlapping_task

from .models import CreditAlert, Invitation, Org, OrgActivity, TopUpCredits


@task(track_started=True, name="send_invitation_email_task")
def send_invitation_email_task(invitation_id):
    invitation = Invitation.objects.get(pk=invitation_id)
    invitation.send_email()


@task(track_started=True, name="send_alert_email_task")
def send_alert_email_task(alert_id):
    alert = CreditAlert.objects.get(pk=alert_id)
    alert.send_email()


@task(track_started=True, name="check_credits_task")
def check_credits_task():  # pragma: needs cover
    CreditAlert.check_org_credits()


@task(track_started=True, name="check_topup_expiration_task")
def check_topup_expiration_task():
    CreditAlert.check_topup_expiration()


@task(track_started=True, name="apply_topups_task")
def apply_topups_task(org_id):
    org = Org.objects.get(id=org_id)
    org.apply_topups()
    org.trigger_send()


@task(track_started=True, name="normalize_contact_tels_task")
def normalize_contact_tels_task(org_id):
    org = Org.objects.get(id=org_id)

    # do we have an org-level country code? if so, try to normalize any numbers not starting with +
    if org.default_country_code:
        urns = ContactURN.objects.filter(org=org, scheme=URN.TEL_SCHEME).exclude(path__startswith="+").iterator()
        for urn in urns:
            urn.ensure_number_normalization(org.default_country_code)


@nonoverlapping_task(track_started=True, name="squash_topupcredits", lock_key="squash_topupcredits", lock_timeout=7200)
def squash_topupcredits():
    TopUpCredits.squash()


@nonoverlapping_task(track_started=True, name="resume_failed_tasks", lock_key="resume_failed_tasks", lock_timeout=7200)
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


@nonoverlapping_task(track_started=True, name="update_org_activity_task")
def update_org_activity(now=None):
    now = now if now else timezone.now()
    OrgActivity.update_day(now)


@nonoverlapping_task(
    track_started=True, name="suspend_topup_orgs_task", lock_key="suspend_topup_orgs_task", lock_timeout=7200
)
def suspend_topup_orgs_task():
    # for every org on a topup plan that isn't suspended, check they have credits, if not, suspend them
    for org in Org.objects.filter(uses_topups=True, is_active=True, is_suspended=False):
        if org.get_credits_remaining() <= 0:
            org.clear_credit_cache()
            if org.get_credits_remaining() <= 0:
                org.is_suspended = True
                org.plan_end = timezone.now()
                org.save(update_fields=["is_suspended", "plan_end"])
