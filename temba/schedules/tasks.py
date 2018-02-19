# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from celery.task import task
from django_redis import get_redis_connection
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from .models import Schedule


@task(track_started=True, name='check_schedule_task')  # pragma: no cover
def check_schedule_task(sched_id=None):
    """
    See if any schedules are expired and fire appropriately
    """
    logger = check_schedule_task.get_logger()

    if sched_id:
        schedules = [Schedule.objects.get(pk=sched_id)]
    else:  # pragma: needs cover
        schedules = Schedule.objects.filter(status='S', is_active=True, next_fire__lt=timezone.now())

    r = get_redis_connection()

    # fire off all expired schedules
    for sched in schedules:
        try:
            # try to acquire a lock
            key = 'fire_schedule_%d' % sched.pk
            if not r.get(key):
                with r.lock(key, timeout=1800):
                    # reget our schedule, it may have been updated
                    sched = Schedule.objects.get(id=sched.pk, status='S', is_active=True, next_fire__lt=timezone.now())

                    if sched and sched.update_schedule():
                        broadcast = sched.get_broadcast()
                        trigger = sched.get_trigger()

                        print("Firing %d" % sched.pk)

                        if broadcast:
                            broadcast.fire()

                        elif trigger:
                            trigger.fire()

                        else:
                            print("Schedule had nothing interesting to fire")

                        # if its one time, delete our schedule
                        if sched.repeat_period == 'O':
                            sched.reset()

        except ObjectDoesNotExist:  # pragma: needs cover
            # this means the schedule already got fired, so perfectly ok, ignore
            pass

        except Exception:  # pragma: no cover
            logger.error("Error running schedule: %s" % sched.pk, exc_info=True)
