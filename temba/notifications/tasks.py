import logging

from temba.utils.crons import cron_task

from .models import Notification, NotificationCount

logger = logging.getLogger(__name__)


@cron_task(lock_timeout=1800)
def send_notification_emails():
    pending = list(
        Notification.objects.filter(email_status=Notification.EMAIL_STATUS_PENDING)
        .select_related("org", "user")
        .order_by("created_on")
    )

    num_sent, num_errored = 0, 0

    for notification in pending:
        try:
            notification.send_email()
            num_sent += 1
        except Exception:  # pragma: no cover
            logger.error("error sending notification email", exc_info=True)
            num_errored += 1

    return {"sent": num_sent, "errored": num_errored}


@cron_task(lock_timeout=1800)
def squash_notification_counts():
    NotificationCount.squash()
