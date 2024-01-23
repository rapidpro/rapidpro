import logging
from datetime import timedelta

from celery import shared_task

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN, ContactURN
from temba.flows.models import ExportFlowResultsTask
from temba.flows.tasks import export_flow_results_task
from temba.msgs.models import ExportMessagesTask
from temba.msgs.tasks import export_messages_task
from temba.utils.crons import cron_task
from temba.utils.email import send_template_email
from temba.utils.text import generate_secret

from .models import Export, Invitation, Org, OrgImport, User, UserSettings


@shared_task
def start_org_import_task(import_id):
    org_import = OrgImport.objects.get(id=import_id)
    org_import.start()


@shared_task
def perform_export(export_id):
    """
    Perform an export
    """
    Export.objects.select_related("org", "created_by").get(id=export_id).perform()


@shared_task
def send_invitation_email_task(invitation_id):
    invitation = Invitation.objects.get(id=invitation_id)
    invitation.send_email()


@shared_task
def send_user_verification_email(user_id):
    user = User.objects.get(id=user_id)
    if user.settings.email_status == UserSettings.STATUS_VERIFIED:
        return

    verification_secret = user.settings.email_verification_secret
    if not verification_secret:
        verification_secret = generate_secret(64)

        user.settings.email_verification_secret = verification_secret
        user.settings.save(update_fields=("email_verification_secret",))

    org = user.get_orgs().first()

    subject = _("%(name)s Email Verification") % org.branding
    template = "orgs/email/email_verification"

    context = dict(org=org, now=timezone.now(), branding=org.branding, secret=verification_secret)
    context["subject"] = subject

    send_template_email(user.email, subject, template, context, org.branding)


@shared_task
def normalize_contact_tels_task(org_id):
    org = Org.objects.get(id=org_id)

    # do we have an org-level country code? if so, try to normalize any numbers not starting with +
    if org.default_country_code:
        urns = ContactURN.objects.filter(org=org, scheme=URN.TEL_SCHEME).exclude(path__startswith="+").iterator()
        for urn in urns:
            urn.ensure_number_normalization(org.default_country_code)


@cron_task(lock_timeout=7200)
def restart_stalled_exports():
    now = timezone.now()
    window = now - timedelta(hours=1)

    exports = Export.objects.filter(modified_on__lte=window).exclude(
        status__in=[Export.STATUS_COMPLETE, Export.STATUS_FAILED]
    )
    for export in exports:
        perform_export.delay(export.pk)

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


@cron_task(lock_timeout=7 * 24 * 60 * 60)
def delete_released_orgs():
    # for each org that was released over 7 days ago, delete it for real
    week_ago = timezone.now() - timedelta(days=Org.DELETE_DELAY_DAYS)

    num_deleted, num_failed = 0, 0

    for org in Org.objects.filter(is_active=False, released_on__lt=week_ago, deleted_on=None):
        start = timezone.now()

        try:
            counts = org.delete()
        except Exception:  # pragma: no cover
            logging.exception(f"exception while deleting '{org.name}' (#{org.id})")
            num_failed += 1
            continue

        seconds = (timezone.now() - start).total_seconds()
        stats = " ".join([f"{k}={v}" for k, v in counts.items()])
        logging.warning(f"successfully deleted '{org.name}' (#{org.id}) in {seconds} seconds ({stats})")
        num_deleted += 1

    return {"deleted": num_deleted, "failed": num_failed}
