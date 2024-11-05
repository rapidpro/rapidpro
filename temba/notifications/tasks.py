import logging

from django.conf import settings
from django.utils import timezone

from temba.utils.crons import cron_task
from temba.utils.models import delete_in_batches

from .models import Notification

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


@cron_task()
def trim_notifications():
    trim_before = timezone.now() - settings.RETENTION_PERIODS["notification"]

    num_deleted = delete_in_batches(Notification.objects.filter(created_on__lt=trim_before))

    return {"deleted": num_deleted}
