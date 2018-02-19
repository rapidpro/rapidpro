# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import time

from celery.task import task
from django.utils import timezone
from temba.msgs.models import Broadcast, Msg, TIMEOUT_EVENT, HANDLER_QUEUE, HANDLE_EVENT_TASK
from temba.orgs.models import Org
from temba.utils.cache import QueueRecord
from temba.utils.dates import datetime_to_epoch
from temba.utils.queues import start_task, complete_task, push_task, nonoverlapping_task
from .models import ExportFlowResultsTask, Flow, FlowStart, FlowRun, FlowStep
from .models import FlowRunCount, FlowNodeCount, FlowPathCount, FlowCategoryCount, FlowPathRecentRun

FLOW_TIMEOUT_KEY = 'flow_timeouts_%y_%m_%d'
logger = logging.getLogger(__name__)


@task(track_started=True, name='send_email_action_task')
def send_email_action_task(org_id, recipients, subject, message):
    org = Org.objects.filter(pk=org_id, is_active=True).first()
    if org:
        org.email_action_send(recipients, subject, message)


@task(track_started=True, name='update_run_expirations_task')  # pragma: no cover
def update_run_expirations_task(flow_id):
    """
    Update all of our current run expirations according to our new expiration period
    """
    for step in FlowStep.objects.filter(run__flow__id=flow_id, run__is_active=True, left_on=None).distinct('run'):  # pragma: needs cover
        step.run.update_expiration(step.arrived_on)

    # force an expiration update
    check_flows_task.apply()


@nonoverlapping_task(track_started=True, name='check_flows_task', lock_key='check_flows')  # pragma: no cover
def check_flows_task():
    """
    See if any flow runs need to be expired
    """
    runs = FlowRun.objects.filter(is_active=True, expires_on__lte=timezone.now())
    FlowRun.bulk_exit(runs, FlowRun.EXIT_TYPE_EXPIRED)


@nonoverlapping_task(track_started=True, name='check_flow_timeouts_task', lock_key='check_flow_timeouts', lock_timeout=3600)  # pragma: no cover
def check_flow_timeouts_task():
    """
    See if any flow runs have timed out
    """
    # find any runs that should have timed out
    runs = FlowRun.objects.filter(is_active=True, timeout_on__lte=timezone.now())
    runs = runs.only('id', 'org', 'timeout_on')

    queued_timeouts = QueueRecord('flow_timeouts', lambda r: '%d:%d' % (r.id, datetime_to_epoch(r.timeout_on)))

    for run in runs:
        # ignore any run which was locked by previous calls to this task
        if not queued_timeouts.is_queued(run):
            try:
                task_payload = dict(type=TIMEOUT_EVENT, run=run.id, timeout_on=run.timeout_on)
                push_task(run.org_id, HANDLER_QUEUE, HANDLE_EVENT_TASK, task_payload)

                queued_timeouts.set_queued([run])
            except Exception:  # pragma: no cover
                logger.error("Error queuing timeout task for run #%d" % run.id, exc_info=True)


@task(track_started=True, name='continue_parent_flows')  # pragma: no cover
def continue_parent_flows(run_ids):
    runs = FlowRun.objects.filter(pk__in=run_ids)
    FlowRun.continue_parent_flow_runs(runs)


@task(track_started=True, name='interrupt_flow_runs_task')
def interrupt_flow_runs_task(flow_id):
    runs = FlowRun.objects.filter(is_active=True, exit_type=None, flow_id=flow_id)
    FlowRun.bulk_exit(runs, FlowRun.EXIT_TYPE_INTERRUPTED)


@task(track_started=True, name='export_flow_results_task')
def export_flow_results_task(export_id):
    """
    Export a flow to a file and e-mail a link to the user
    """
    ExportFlowResultsTask.objects.select_related('org').get(id=export_id).perform()


@task(track_started=True, name='start_flow_task')
def start_flow_task(start_id):
    flow_start = FlowStart.objects.get(pk=start_id)
    flow_start.start()


@task(track_started=True, name='start_msg_flow_batch')
def start_msg_flow_batch_task():
    # pop off the next task
    org_id, task_obj = start_task(Flow.START_MSG_FLOW_BATCH)

    # it is possible that somehow we might get None back if more workers were started than tasks got added, bail if so
    if task_obj is None:  # pragma: needs cover
        return

    start = time.time()

    try:
        # instantiate all the objects we need that were serialized as JSON
        flow = Flow.objects.filter(pk=task_obj['flow'], is_active=True).first()
        if not flow:  # pragma: needs cover
            return

        broadcasts = [] if not task_obj['broadcasts'] else Broadcast.objects.filter(pk__in=task_obj['broadcasts'])
        started_flows = [] if not task_obj['started_flows'] else task_obj['started_flows']
        start_msg = None if not task_obj['start_msg'] else Msg.objects.filter(id=task_obj['start_msg']).first()
        extra = task_obj['extra']
        flow_start = None if not task_obj['flow_start'] else FlowStart.objects.filter(pk=task_obj['flow_start']).first()
        contacts = task_obj['contacts']

        # and go do our work
        flow.start_msg_flow_batch(contacts, broadcasts=broadcasts,
                                  started_flows=started_flows, start_msg=start_msg,
                                  extra=extra, flow_start=flow_start)
    finally:
        complete_task(Flow.START_MSG_FLOW_BATCH, org_id)

    print("Started batch of %d contacts in flow %d [%d] in %0.3fs"
          % (len(contacts), flow.id, flow.org_id, time.time() - start))


@nonoverlapping_task(track_started=True, name="squash_flowpathcounts", lock_key='squash_flowpathcounts')
def squash_flowpathcounts():
    FlowPathCount.squash()


@nonoverlapping_task(track_started=True, name="squash_flowruncounts", lock_key='squash_flowruncounts')
def squash_flowruncounts():
    FlowNodeCount.squash()
    FlowRunCount.squash()
    FlowCategoryCount.squash()
    FlowPathRecentRun.prune()


@task(track_started=True, name="deactivate_flow_runs_task")
def deactivate_flow_runs_task(flow_id):
    flow = Flow.objects.get(id=flow_id)
    flow.deactivate_runs()
