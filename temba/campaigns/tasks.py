from __future__ import unicode_literals

from datetime import datetime
from django.utils import timezone
from djcelery_transactions import task
from redis_cache import get_redis_connection
from .models import Campaign, EventFire
from django.conf import settings
import redis

@task(track_started=True, name='check_campaigns_task')  # pragma: no cover
def check_campaigns_task(sched_id=None):
    """
    See if any event fires need to be triggered
    """
    logger = check_campaigns_task.get_logger()

    # get a lock
    r = get_redis_connection()

    key = 'check_campaigns'

    # only do this if we aren't already checking campaigns
    if not r.get(key):
        with r.lock(key, timeout=3600):
            # for each that needs to be fired
            for fire in EventFire.objects.filter(fired=None, scheduled__lte=timezone.now()):
                try:
                    key = 'fire_campaign_%d' % fire.pk
                    if not r.get(key):
                        # try to acquire a lock
                        with r.lock('fire_campaign_%d' % fire.pk, timeout=120):
                            # reload it
                            fire = EventFire.objects.get(id=fire.pk)
                            if not fire.fired:
                                fire.fire()

                except:  # pragma: no cover
                    logger.error("Error running campaign event: %s" % fire.pk, exc_info=True)
