from redis_cache import get_redis_connection
from celery import current_app
from temba.utils import dict_to_json
from django.conf import settings
import json
import time
import importlib

LOW_PRIORITY = +10000000   # +10M ~ 110 days
DEFAULT_PRIORITY = 0
HIGH_PRIORITY = -10000000  # -10M ~ 110 days
HIGHER_PRIORITY = -20000000  # -20M ~ 220 days


def push_task(org, queue, task_name, args, priority=DEFAULT_PRIORITY):
    """
    Adds a task to queue_name with the supplied arguments.

    Ex: add_task(nyaruka, 'flows', 'start_flow', [1,2,3,4,5,6,7,8,9,10])
    """
    r = get_redis_connection('default')

    # calculate our score from the current time and priority, this could get us in trouble
    # if things are queued for more than ~100 days, but otherwise gives us the properties of prioritizing
    # first based on priority, then insertion order.
    score = time.time() + priority

    # push our task onto the right queue and make sure it is in the active list (atomically)
    with r.pipeline() as pipe:
        key = "%s:%d" % (task_name, org.id)
        pipe.zadd(key, dict_to_json(args), score)

        # and make sure this key is in our list of queues so this job will get worked on
        pipe.sadd("%s:active" % task_name, key)
        pipe.execute()

    # if we were given a queue to schedule on, then add this task to celery.
    #
    # note that the task that is fired needs no arguments as it should just use pop_task with the
    # task name to determine what to work on.
    if queue:
        if getattr(settings, 'CELERY_ALWAYS_EAGER', False):
            task_function = lookup_task_function(task_name)
            task_function()
        else:
            current_app.send_task(task_name, args=[], kwargs={}, queue=queue)


def pop_task(task_name):
    """
    Pops the next 'random' task off our queue, returning the arguments that were saved

    Ex: pop_next_task('start_flow')
    <<< {flow=5, contacts=[1,2,3,4,5,6,7,8,9,10]}
    """
    r = get_redis_connection('default')

    task = None
    active_set = "%s:active" % task_name

    # get what queue we will work against
    queue = r.srandmember(active_set)

    while queue:
        # this lua script does both a "zpop" (popping the next highest thing off our sorted set) and
        # a clearing of our active set if there is no value in it as an atomic action
        lua = "local val = redis.call('zrange', ARGV[2], 0, 0) \n" \
              "if next(val) == nil then redis.call('srem', ARGV[1], ARGV[2]) return nil \n"\
              "else redis.call('zremrangebyrank', ARGV[2], 0, 0) return val[1] end\n"

        task = r.eval(lua, 2, 'active_set', 'queue', active_set, queue)

        # found a task? then break out
        if task is not None:
            task = json.loads(task)
            break

        # if we didn't get a task, then run again against a new queue until there is nothing left in our task queue
        queue = r.srandmember(active_set)

    return task


def lookup_task_function(task_name):
    """
    Because Celery doesn't support using send_task() when ALWAYS_EAGER is on and we still want all our queue
    functionality to work in testing environments, we use a map in our settings to go from task name to function
    and call our tasks manually. This takes care of that.
    """
    task_map = getattr(settings, 'CELERY_TASK_MAP', None)
    if not task_map:
        print "Empty or missing CELERY_TASK_MAP in settings.py, unable to find task for %s" % task_name

    task_function = task_map.get(task_name, None)
    if not task_function:
        raise Exception("Unable to find '%s' task in settings.CELERY_TASK_MAP, aborting" % task_name)

    m, f = task_function.rsplit('.', 1)
    mod = importlib.import_module(m)
    return getattr(mod, f)
