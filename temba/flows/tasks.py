from __future__ import print_function, unicode_literals

import time

from celery.task import task
from django.utils import timezone
from django_redis import get_redis_connection
from datetime import timedelta
from temba.flows.models import FlowStatsCache
from temba.msgs.models import Broadcast, Msg, TIMEOUT_EVENT, HANDLER_QUEUE, HANDLE_EVENT_TASK
from temba.orgs.models import Org
from temba.utils import datetime_to_epoch
from temba.utils.queues import start_task, complete_task
from temba.utils.queues import push_task, nonoverlapping_task
from .models import ExportFlowResultsTask, Flow, FlowStart, FlowRun, FlowStep, FlowRunCount, FlowPathCount, FlowPathRecentStep

FLOW_TIMEOUT_KEY = 'flow_timeouts_%y_%m_%d'


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
    r = get_redis_connection()

    # find any runs that should have timed out
    runs = FlowRun.objects.filter(is_active=True, timeout_on__lte=timezone.now())
    runs = runs.only('id', 'org', 'timeout_on')
    for run in runs:
        run_key = '%d:%d' % (run.id, datetime_to_epoch(run.timeout_on))

        # check whether we have already queued this timeout
        pipe = r.pipeline()
        pipe.sismember(timezone.now().strftime(FLOW_TIMEOUT_KEY), run_key)
        pipe.sismember((timezone.now() - timedelta(days=1)).strftime(FLOW_TIMEOUT_KEY), run_key)
        (queued_today, queued_yesterday) = pipe.execute()

        # if not, add a task to handle the timeout
        if not queued_today and not queued_yesterday:
            push_task(run.org_id, HANDLER_QUEUE, HANDLE_EVENT_TASK,
                      dict(type=TIMEOUT_EVENT, run=run.id, timeout_on=run.timeout_on))

            # tag this run as being worked on so we don't double queue
            pipe = r.pipeline()
            sent_key = timezone.now().strftime(FLOW_TIMEOUT_KEY)
            pipe.sadd(sent_key, run_key)
            pipe.expire(sent_key, 86400)
            pipe.execute()


@task(track_started=True, name='continue_parent_flows')  # pragma: no cover
def continue_parent_flows(run_ids):
    runs = FlowRun.objects.filter(pk__in=run_ids)
    FlowRun.continue_parent_flow_runs(runs)


@task(track_started=True, name='export_flow_results_task')
def export_flow_results_task(id):
    """
    Export a flow to a file and e-mail a link to the user
    """
    export_task = ExportFlowResultsTask.objects.filter(pk=id).first()
    if export_task:
        export_task.perform()


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
        start_msg = None if not task_obj['start_msg'] else Msg.objects.filter(pk=task_obj['start_msg']).first()
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


@task(track_started=True, name="check_flow_stats_accuracy_task")
def check_flow_stats_accuracy_task(flow_id):
    logger = check_flow_stats_accuracy_task.get_logger()

    flow = Flow.objects.get(pk=flow_id)

    r = get_redis_connection()
    runs_started_cached = r.get(flow.get_stats_cache_key(FlowStatsCache.runs_started_count))
    runs_started_cached = 0 if runs_started_cached is None else int(runs_started_cached)
    runs_started = flow.runs.filter(contact__is_test=False).count()

    if runs_started != runs_started_cached:
        # log error that we had to rebuild, shouldn't be happening
        logger.error('Rebuilt flow stats (Org: %d, Flow: %d). Cache was %d but should be %d.'
                     % (flow.org.pk, flow.pk, runs_started_cached, runs_started))

        calculate_flow_stats_task.delay(flow.pk)


@task(track_started=True, name="calculate_flow_stats")
def calculate_flow_stats_task(flow_id):
    r = get_redis_connection()

    flow = Flow.objects.get(pk=flow_id)
    runs_started_cached = r.get(flow.get_stats_cache_key(FlowStatsCache.runs_started_count))
    runs_started_cached = 0 if runs_started_cached is None else int(runs_started_cached)
    runs_started = flow.runs.filter(contact__is_test=False).count()

    if runs_started != runs_started_cached:
        Flow.objects.get(pk=flow_id).do_calculate_flow_stats()


@nonoverlapping_task(track_started=True, name="squash_flowpathcounts", lock_key='squash_flowpathcounts')
def squash_flowpathcounts():
    FlowPathCount.squash()


@nonoverlapping_task(track_started=True, name="prune_flowpathrecentsteps")
def prune_flowpathrecentsteps():
    FlowPathRecentStep.prune()


@nonoverlapping_task(track_started=True, name="squash_flowruncounts", lock_key='squash_flowruncounts')
def squash_flowruncounts():
    FlowRunCount.squash()


@task(track_started=True, name="delete_flow_results_task")
def delete_flow_results_task(flow_id):
    flow = Flow.objects.get(id=flow_id)
    flow.delete_results()
