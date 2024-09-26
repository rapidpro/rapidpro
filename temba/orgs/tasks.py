import logging
from datetime import timedelta

from celery import shared_task
from django_redis import get_redis_connection

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN, ContactURN
from temba.utils.crons import cron_task
from temba.utils.email import EmailSender

from .models import Export, Invitation, Org, OrgImport, User, UserSettings


@shared_task
def perform_import(import_id):
    OrgImport.objects.get(id=import_id).perform()


@shared_task
def perform_export(export_id):
    """
    Perform an export
    """
    Export.objects.select_related("org", "created_by").get(id=export_id).perform()


@shared_task
def send_user_verification_email(org_id, user_id):
    r = get_redis_connection()
    org = Org.objects.get(id=org_id)
    user = User.objects.get(id=user_id)

    assert user in org.get_users()

    if user.settings.email_status == UserSettings.STATUS_VERIFIED:
        return

    key = f"send_verification_email:{user.email}".lower()

    if r.exists(key):
        return

    sender = EmailSender.from_email_type(org.branding, "notifications")
    sender.send(
        [user.email],
        _("%(name)s Email Verification") % org.branding,
        "orgs/email/email_verification",
        {"org": org, "secret": user.settings.email_verification_secret},
    )

    r.set(key, "1", ex=60 * 10)


@shared_task
def normalize_contact_tels_task(org_id):
    org = Org.objects.get(id=org_id)

    # do we have an org-level country code? if so, try to normalize any numbers not starting with +
    if org.default_country_code:
        urns = ContactURN.objects.filter(org=org, scheme=URN.TEL_SCHEME).exclude(path__startswith="+").iterator()
        for urn in urns:
            urn.ensure_number_normalization(org.default_country_code)


@cron_task()
def trim_exports():
    trim_before = timezone.now() - settings.RETENTION_PERIODS["export"]

    num_deleted = 0
    for export in Export.objects.filter(created_on__lt=trim_before):
        export.delete()
        num_deleted += 1

    return {"deleted": num_deleted}


@cron_task(lock_timeout=7200)
def restart_stalled_exports():
    now = timezone.now()
    window = now - timedelta(hours=1)

    exports = Export.objects.filter(modified_on__lte=window).exclude(
        status__in=[Export.STATUS_COMPLETE, Export.STATUS_FAILED]
    )
    for export in exports:
        perform_export.delay(export.id)


@cron_task(lock_timeout=7200)
def expire_invitations():
    # delete any invitations that are no longer valid
    expire_before = timezone.now() - settings.INVITATION_VALIDITY
    num_expired = 0
    for invitation in Invitation.objects.filter(created_on__lt=expire_before, is_active=True):
        invitation.release()
        num_expired += 1

    return {"expired": num_expired}


@cron_task(lock_timeout=7 * 24 * 60 * 60)
def delete_released_orgs():
    # for each org that was released over 7 days ago, delete it for real
    week_ago = timezone.now() - timedelta(days=Org.DELETE_DELAY_DAYS)

    num_deleted, num_failed = 0, 0

    for org in Org.objects.filter(is_active=False, released_on__lt=week_ago, deleted_on=None).order_by("released_on"):
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
