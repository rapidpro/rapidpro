import time
from enum import Enum

from django_redis import get_redis_connection

from django.utils import timezone

from temba.utils import json

HIGH_PRIORITY = -10000000
DEFAULT_PRIORITY = 0

QUEUE_PATTERN = "%s:%d"
ACTIVE_PATTERN = "%s:active"

# task queues
CONTACT_QUEUE = "c:%d:%d"
BATCH_QUEUE = "batch"
HANDLER_QUEUE = "handler"


class HandlerTask(Enum):
    CONTACT_EVENT = "handle_contact_event"


class ContactEvent(Enum):
    MSG = "msg_event"
    MO_MISS = "mo_miss"


class BatchTask(Enum):
    START_FLOW = "start_flow"
    SEND_BROADCAST = "send_broadcast"
    INTERRUPT_SESSIONS = "interrupt_sessions"
    POPULATE_DYNAMIC_GROUP = "populate_dynamic_group"


def queue_msg_handling(msg):
    """
    Queues the passed in message for handling in mailroom
    """

    msg_task = {
        "org_id": msg.org_id,
        "channel_id": msg.channel_id,
        "contact_id": msg.contact_id,
        "msg_id": msg.id,
        "msg_uuid": str(msg.uuid),
        "msg_external_id": msg.external_id,
        "urn": str(msg.contact_urn),
        "urn_id": msg.contact_urn_id,
        "text": msg.text,
        "attachments": msg.attachments,
        "new_contact": getattr(msg.contact, "is_new", False),
    }

    _queue_handler_task(msg.org_id, msg.contact_id, ContactEvent.MSG, msg_task)


def queue_mo_miss_event(event):
    """
    Queues the passed in channel event to mailroom for handling
    """

    event_task = {
        "id": event.id,
        "event_type": ContactEvent.MO_MISS.value,
        "org_id": event.org_id,
        "channel_id": event.channel_id,
        "contact_id": event.contact_id,
        "urn": str(event.contact_urn),
        "urn_id": event.contact_urn_id,
        "extra": event.extra,
        "new_contact": getattr(event.contact, "is_new", False),
    }

    _queue_handler_task(event.org_id, event.contact_id, ContactEvent.MO_MISS, event_task)


def queue_broadcast(broadcast):
    """
    Queues the passed in broadcast for sending by mailroom
    """

    task = {
        "translations": {lang: {"text": text} for lang, text in broadcast.text.items()},
        "template_state": broadcast.get_template_state(),
        "base_language": broadcast.base_language,
        "urns": [u.urn for u in broadcast.urns.all()],
        "contact_ids": list(broadcast.contacts.values_list("id", flat=True)),
        "group_ids": list(broadcast.groups.values_list("id", flat=True)),
        "broadcast_id": broadcast.id,
        "org_id": broadcast.org_id,
    }

    _queue_batch_task(broadcast.org_id, BatchTask.SEND_BROADCAST, task, HIGH_PRIORITY)


def queue_populate_dynamic_group(group):
    """
    Queues a task to populate the contacts for a dynamic group
    """
    task = {"group_id": group.id, "query": group.query, "org_id": group.org_id}

    _queue_batch_task(group.org_id, BatchTask.POPULATE_DYNAMIC_GROUP, task, HIGH_PRIORITY)


def queue_flow_start(start):
    """
    Queues the passed in flow start for starting by mailroom
    """

    org_id = start.flow.org_id

    task = {
        "start_id": start.id,
        "start_type": start.start_type,
        "org_id": org_id,
        "created_by": start.created_by.username,
        "flow_id": start.flow_id,
        "flow_type": start.flow.flow_type,
        "contact_ids": list(start.contacts.values_list("id", flat=True)),
        "group_ids": list(start.groups.values_list("id", flat=True)),
        "query": start.query,
        "restart_participants": start.restart_participants,
        "include_active": start.include_active,
        "extra": start.extra,
    }

    _queue_batch_task(org_id, BatchTask.START_FLOW, task, HIGH_PRIORITY)


def queue_interrupt(org, *, contacts=None, channel=None, flow=None, session=None):
    """
    Queues an interrupt task for handling by mailroom
    """

    assert (
        contacts or channel or flow or session
    ), "must specify either a set of contacts or a channel or a flow or a session"

    task = {}
    if contacts:
        task["contact_ids"] = [c.id for c in contacts]
    if channel:
        task["channel_ids"] = [channel.id]
    if flow:
        task["flow_ids"] = [flow.id]
    if session:
        task["session_ids"] = [session.id]

    _queue_batch_task(org.id, BatchTask.INTERRUPT_SESSIONS, task, HIGH_PRIORITY)


def _queue_batch_task(org_id, task_type, task, priority):
    """
    Adds the passed in task to the mailroom batch queue
    """

    r = get_redis_connection("default")
    pipe = r.pipeline()
    _queue_task(pipe, org_id, BATCH_QUEUE, task_type, task, priority)
    pipe.execute()


def _queue_handler_task(org_id, contact_id, task_type, task):
    """
    Adds the passed in task to the contact's queue for mailroom to process
    """

    contact_queue = CONTACT_QUEUE % (org_id, contact_id)
    contact_task = _create_mailroom_task(org_id, task_type, task)

    r = get_redis_connection("default")
    pipe = r.pipeline()

    # push our concrete task to the contact's queue
    pipe.rpush(contact_queue, json.dumps(contact_task))

    # then push a contact handling event to the org queue
    event_task = {"contact_id": contact_id}
    _queue_task(pipe, org_id, HANDLER_QUEUE, HandlerTask.CONTACT_EVENT, event_task, HIGH_PRIORITY)
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
    payload = _create_mailroom_task(org_id, task_type, task)

    org_queue = QUEUE_PATTERN % (queue, org_id)
    active_queue = ACTIVE_PATTERN % queue

    # push onto our org queue
    pipe.zadd(org_queue, score, json.dumps(payload))

    # and mark that org as active
    pipe.zincrby(active_queue, org_id, 0)


def _create_mailroom_task(org_id, task_type, task):
    """
    Returns a mailroom format task job based on the task type and passed in task
    """
    return {"type": task_type.value, "org_id": org_id, "task": task, "queued_on": timezone.now()}
