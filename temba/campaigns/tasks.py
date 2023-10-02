from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from temba.campaigns.models import EventFire
from temba.utils.crons import cron_task
from temba.utils.models import delete_in_batches


@cron_task()
def trim_event_fires():
    start = timezone.now()

    def can_continue():
        return (timezone.now() - start) < timedelta(minutes=5)

    # first delete any unfired fires for inactive events - these aren't retained for any period
    num_deleted = delete_in_batches(
        EventFire.objects.filter(fired=None, event__is_active=False), post_delete=can_continue
    )

    # secondly (if we have time left) delete any fired fires that are older than the retention period
    if can_continue():
        trim_before = timezone.now() - settings.RETENTION_PERIODS["eventfire"]

        num_deleted += delete_in_batches(EventFire.objects.filter(fired__lt=trim_before), post_delete=can_continue)

    return {"deleted": num_deleted}
