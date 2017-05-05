from __future__ import unicode_literals

import logging
import six

from celery.task import task
from collections import defaultdict
from django.db import transaction
from django.utils import timezone
from django_redis import get_redis_connection
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.msgs.models import HANDLER_QUEUE, HANDLE_EVENT_TASK, FIRE_EVENT
from temba.utils.queues import push_task, nonoverlapping_task


logger = logging.getLogger(__name__)


@nonoverlapping_task(track_started=True, name='check_campaigns_task', lock_key='check_campaigns')
def check_campaigns_task():
    """
    See if any event fires need to be triggered
    """
    unfired = EventFire.objects.filter(fired=None, scheduled__lte=timezone.now())
    unfired = unfired.select_related('contact', 'event')

    # group fire events by flow so they can be batched
    fires_by_flow_id = defaultdict(list)
    for fire in unfired:
        fires_by_flow_id[fire.event.flow_id].append(fire)

    # create queued tasks
    for flow_id, fires in six.iteritems(fires_by_flow_id):
        try:
            push_task(fire.contact.org_id, HANDLER_QUEUE, HANDLE_EVENT_TASK, dict(type=FIRE_EVENT, fires=[f.id for f in fires]))

        except Exception:  # pragma: no cover
            fire_ids_str = ','.join(six.text_type(f.id) for f in fires)
            logger.error("Error queuing campaign event fires: %s" % fire_ids_str, exc_info=True)


@task(track_started=True, name='update_event_fires_task')  # pragma: no cover
def update_event_fires(event_id):

    # get a lock
    r = get_redis_connection()
    key = 'event_fires_event_%d' % event_id

    with r.lock(key, timeout=300):
        try:

            with transaction.atomic():
                event = CampaignEvent.objects.filter(pk=event_id).first()
                if event:
                    EventFire.do_update_eventfires_for_event(event)

        except Exception as e:  # pragma: no cover

            # requeue our task to try again in five minutes
            update_event_fires(event_id).delay(countdown=60 * 5)

            # bubble up the exception so sentry sees it
            raise e


@task(track_started=True, name='update_event_fires_for_campaign_task')  # pragma: no cover
def update_event_fires_for_campaign(campaign_id):

    # get a lock
    r = get_redis_connection()
    key = 'event_fires_campaign_%d' % campaign_id

    with r.lock(key, timeout=300):
        try:
            with transaction.atomic():
                campaign = Campaign.objects.filter(pk=campaign_id).first()
                if campaign:
                    EventFire.do_update_campaign_events(campaign)

        except Exception as e:  # pragma: no cover
            import traceback
            traceback.print_exc(e)

            # requeue our task to try again in five minutes
            update_event_fires_for_campaign(campaign_id).delay(countdown=60 * 5)

            # bubble up the exception so sentry sees it
            raise e
