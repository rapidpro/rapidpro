import time
from enum import Enum

from django_redis import get_redis_connection

from django.utils import timezone

from temba.utils import json

HIGH_PRIORITY = -10000000
DEFAULT_PRIORITY = 0

QUEUE_PATTERN = "%s:%d"
ACTIVE_PATTERN = "%s:active"


class BatchTask(Enum):
    START_FLOW = "start_flow"
    INTERRUPT_SESSIONS = "interrupt_sessions"
    POPULATE_DYNAMIC_GROUP = "populate_dynamic_group"
    SCHEDULE_CAMPAIGN_EVENT = "schedule_campaign_event"
    IMPORT_CONTACT_BATCH = "import_contact_batch"
    INTERRUPT_CHANNEL = "interrupt_channel"


def queue_populate_dynamic_group(group):
    """
    Queues a task to populate the contacts for a dynamic group
    """
    task = {"group_id": group.id, "query": group.query, "org_id": group.org_id}

    _queue_batch_task(group.org_id, BatchTask.POPULATE_DYNAMIC_GROUP, task, HIGH_PRIORITY)


def queue_schedule_campaign_event(event):
    """
    Queues a task to schedule a new campaign event for all contacts in the campaign
    """

    org_id = event.campaign.org_id
    task = {"org_id": org_id, "campaign_event_id": event.id}

    _queue_batch_task(org_id, BatchTask.SCHEDULE_CAMPAIGN_EVENT, task, HIGH_PRIORITY)


def queue_flow_start(start):
    """
    Queues the passed in flow start for starting by mailroom
    """

    org_id = start.flow.org_id

    task = {
        "start_id": start.id,
        "start_type": start.start_type,
        "org_id": org_id,
        "created_by_id": start.created_by_id,
        "flow_id": start.flow_id,
        "contact_ids": list(start.contacts.values_list("id", flat=True)),
        "group_ids": list(start.groups.values_list("id", flat=True)),
        "urns": start.urns or [],
        "query": start.query,
        "exclusions": start.exclusions,
        "params": start.params,
    }

    _queue_batch_task(org_id, BatchTask.START_FLOW, task, HIGH_PRIORITY)


def queue_contact_import_batch(batch):
    """
    Queues a task to import a batch of contacts
    """

    task = {"contact_import_batch_id": batch.id}

    _queue_batch_task(batch.contact_import.org.id, BatchTask.IMPORT_CONTACT_BATCH, task, DEFAULT_PRIORITY)


def queue_interrupt_channel(org, channel):
    """
    Queues an interrupt channel task for handling by mailroom
    """

    task = {"channel_id": channel.id}

    _queue_batch_task(org.id, BatchTask.INTERRUPT_CHANNEL, task, HIGH_PRIORITY)


def queue_interrupt(org, *, contacts=None, flow=None, sessions=None):
    """
    Queues an interrupt task for handling by mailroom
    """

    assert contacts or flow or sessions, "must specify either a set of contacts or a flow or sessions"

    task = {}
    if contacts:
        task["contact_ids"] = [c.id for c in contacts]
    if flow:
        task["flow_ids"] = [flow.id]
    if sessions:
        task["session_ids"] = [s.id for s in sessions]

    _queue_batch_task(org.id, BatchTask.INTERRUPT_SESSIONS, task, HIGH_PRIORITY)


def _queue_batch_task(org_id, task_type, task, priority):
    """
    Adds the passed in task to the mailroom batch queue
    """

    r = get_redis_connection("default")
    pipe = r.pipeline()
    _queue_task(pipe, org_id, "batch", task_type, task, priority)
    pipe.execute()


def _queue_task(pipe, org_id, queue, task_type, task, priority):
    """
    Queues a task to mailroom

    Args:
        pipe: an open redis pipe
        org_id: the id of the org for this task
        queue: the queue the task should be added to
        task_type: the type of the task
        task: the task definition
        priority: the priority of this task

    """

    # our score is the time in milliseconds since epoch + any priority modifier
    score = int(round(time.time() * 1000)) + priority

    # create our payload
    payload = _create_mailroom_task(task_type, task)

    org_queue = QUEUE_PATTERN % (queue, org_id)
    active_queue = ACTIVE_PATTERN % queue

    # push onto our org queue
    pipe.zadd(org_queue, {json.dumps(payload): score})

    # and mark that org as active
    pipe.zincrby(active_queue, 0, org_id)


def _create_mailroom_task(task_type, task):
    """
    Returns a mailroom format task job based on the task type and passed in task
    """
    return {"type": task_type.value, "task": task, "queued_on": timezone.now()}
