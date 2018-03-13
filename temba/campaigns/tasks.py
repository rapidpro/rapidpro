# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import six

from celery.task import task
from collections import defaultdict
from django.db import transaction
from django.utils import timezone
from django_redis import get_redis_connection
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.msgs.models import HANDLER_QUEUE, HANDLE_EVENT_TASK, FIRE_EVENT
from temba.utils import chunk_list
from temba.utils.cache import QueueRecord
from temba.utils.queues import push_task, nonoverlapping_task


logger = logging.getLogger(__name__)


@nonoverlapping_task(track_started=True, name='check_campaigns_task', lock_key='check_campaigns')
def check_campaigns_task():
    """
    See if any event fires need to be triggered
    """
    from temba.flows.models import Flow

    unfired = EventFire.objects.filter(fired=None, scheduled__lte=timezone.now())
    unfired = unfired.values('id', 'event__flow_id')

    # group fire events by flow so they can be batched
    fire_ids_by_flow_id = defaultdict(list)
    for fire in unfired:
        fire_ids_by_flow_id[fire['event__flow_id']].append(fire['id'])

    # fetch the flows used by all these event fires
    flows_by_id = {flow.id: flow for flow in Flow.objects.filter(id__in=fire_ids_by_flow_id.keys())}

    queued_fires = QueueRecord('queued_event_fires')

    # create queued tasks
    for flow_id, fire_ids in six.iteritems(fire_ids_by_flow_id):
        flow = flows_by_id[flow_id]

        # create sub-batches no no single task is too big
        for fire_id_batch in chunk_list(fire_ids, 500):

            # ignore any fires which were queued by previous calls to this task but haven't yet been marked as fired
            queued_fire_ids = queued_fires.filter_unqueued(fire_id_batch)

            if queued_fire_ids:
                try:
                    push_task(flow.org_id, HANDLER_QUEUE, HANDLE_EVENT_TASK, dict(type=FIRE_EVENT, fires=queued_fire_ids))

                    queued_fires.set_queued(queued_fire_ids)
                except Exception:  # pragma: no cover
                    fire_ids_str = ','.join(six.text_type(f) for f in queued_fire_ids)
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
            traceback.print_exc()

            # requeue our task to try again in five minutes
            update_event_fires_for_campaign(campaign_id).delay(countdown=60 * 5)

            # bubble up the exception so sentry sees it
            raise e
