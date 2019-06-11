from django_redis import get_redis_connection

from django.utils import timezone

from celery.task import task

from .models import Schedule


@task(track_started=True, name="check_schedule_task")
def check_schedule_task(sched_id=None):
    """
    See if any schedules are expired and fire appropriately
    """
    logger = check_schedule_task.get_logger()

    schedules = Schedule.objects.filter(status="S", is_active=True, next_fire__lt=timezone.now())

    if sched_id:
        schedules = schedules.filter(id=sched_id)

    r = get_redis_connection()

    # fire off all expired schedules
    for sched in schedules:
        try:
            # try to acquire a lock
            key = f"fire_schedule_{sched.id}"
            if not r.get(key):
                with r.lock(key, timeout=1800):
                    # refetch our schedule as it may have been updated
                    sched = Schedule.objects.filter(
                        id=sched.id, status="S", is_active=True, next_fire__lt=timezone.now()
                    ).first()

                    if sched and sched.update_schedule():
                        sched.fire()

        except Exception:  # pragma: no cover
            logger.error("Error firing schedule: %s" % sched.id, exc_info=True)
