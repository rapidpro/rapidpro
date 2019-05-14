import time

from django_redis import get_redis_connection

from temba.utils import json

HIGH_PRIORITY = -10000000
DEFAULT_PRIORITY = 0

QUEUE_PATTERN = "%s:%d"
ACTIVE_PATTERN = "%s:active"

BATCH_QUEUE = "batch"

START_FLOW_TASK = "start_flow"


def queue_mailroom_task(org_id, queue, task_type, task, priority):
    """
    Adds the passed in task to the proper mailroom queue
    """
    # our score is the time in milliseconds since epoch + any priority modifier
    score = int(round(time.time() * 1000)) + priority

    r = get_redis_connection("default")

    # create our payload
    payload = json.dumps({"type": task_type, "org_id": org_id, "task": task})

    orgQueue = QUEUE_PATTERN % (queue, org_id)
    activeQueue = ACTIVE_PATTERN % queue

    # push onto each
    r.zadd(orgQueue, score, payload)
    r.zincrby(activeQueue, org_id, 0)
