from __future__ import unicode_literals

from django.utils import timezone
from django_redis import get_redis_connection
from djcelery_transactions import task
from temba.flows.models import FlowStatsCache
from temba.msgs.models import Broadcast, Msg, TIMEOUT_EVENT, HANDLER_QUEUE, HANDLE_EVENT_TASK
from temba.utils.email import send_simple_email
from temba.utils.queues import pop_task
from temba.utils.queues import push_task, nonoverlapping_task
from .models import ExportFlowResultsTask, Flow, FlowStart, FlowRun, FlowStep, FlowRunCount


@task(track_started=True, name='send_email_action_task')
def send_email_action_task(recipients, subject, message):
    send_simple_email(recipients, subject, message)


@task(track_started=True, name='update_run_expirations_task')  # pragma: no cover
def update_run_expirations_task(flow_id):
    """
    Update all of our current run expirations according to our new expiration period
    """
    for step in FlowStep.objects.filter(run__flow__id=flow_id, run__is_active=True, left_on=None).distinct('run'):
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


@nonoverlapping_task(track_started=True, name='check_flow_timeouts_task', lock_key='check_flow_timeouts')  # pragma: no cover
def check_flow_timeouts_task():
    """
    See if any flow runs have timed out
    """
    # find any runs that should have timed out
    runs = FlowRun.objects.filter(is_active=True, timeout_on__lte=timezone.now())
    runs = runs.only('id', 'org', 'timeout_on')
    for run in runs:
        # move this flow forward via the handler queue
        push_task(run.org_id, HANDLER_QUEUE, HANDLE_EVENT_TASK, dict(type=TIMEOUT_EVENT, run=run.id, timeout_on=run.timeout_on))


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
        export_task.start_export()


@task(track_started=True, name='start_flow_task')
def start_flow_task(start_id):
    flow_start = FlowStart.objects.get(pk=start_id)
    flow_start.start()


@task(track_started=True, name='start_msg_flow_batch')
def start_msg_flow_batch_task():
    # pop off the next task
    task_obj = pop_task('start_msg_flow_batch')

    # it is possible that somehow we might get None back if more workers were started than tasks got added, bail if so
    if task_obj is None:
        return

    # instantiate all the objects we need that were serialized as JSON
    flow = Flow.objects.filter(pk=task_obj['flow'], is_active=True).first()
    if not flow:
        return

    broadcasts = [] if not task_obj['broadcasts'] else Broadcast.objects.filter(pk__in=task_obj['broadcasts'])
    started_flows = [] if not task_obj['started_flows'] else task_obj['started_flows']
    start_msg = None if not task_obj['start_msg'] else Msg.objects.filter(pk=task_obj['start_msg']).first()
    extra = task_obj['extra']
    flow_start = None if not task_obj['flow_start'] else FlowStart.objects.filter(pk=task_obj['flow_start']).first()

    # and go do our work
    flow.start_msg_flow_batch(task_obj['contacts'], broadcasts=broadcasts,
                              started_flows=started_flows, start_msg=start_msg,
                              extra=extra, flow_start=flow_start)


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


@nonoverlapping_task(track_started=True, name="squash_flowruncounts", lock_key='squash_flowruncounts')
def squash_flowruncounts():
    FlowRunCount.squash_counts()


@task(track_started=True, name="delete_flow_results_task")
def delete_flow_results_task(flow_id):
    flow = Flow.objects.get(id=flow_id)
    flow.delete_results()
