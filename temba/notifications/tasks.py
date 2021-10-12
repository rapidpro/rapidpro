import logging

from django.utils import timezone

from temba.utils.celery import nonoverlapping_task

from .models import Notification, NotificationCount

logger = logging.getLogger(__name__)


@nonoverlapping_task(track_started=True, name="send_notification_emails", lock_timeout=1800)
def send_notification_emails():
    pending = list(
        Notification.objects.filter(email_status=Notification.EMAIL_STATUS_PENDING)
        .select_related("org", "user")
        .order_by("created_on")
    )
    start = timezone.now()

    for notification in pending:
        try:
            notification.send_email()
        except Exception:  # pragma: no cover
            logger.error(f"error sending notification email", exc_info=True)

    if pending:
        time_taken = (timezone.now() - start).total_seconds()
        logger.info(f"{len(pending)} notification emails sent in {time_taken} seconds")


@nonoverlapping_task(track_started=True, name="squash_notificationcounts", lock_timeout=1800)
def squash_notificationcounts():
    NotificationCount.squash()
