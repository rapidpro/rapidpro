# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import time
import importlib

from celery import current_app, shared_task
from django.conf import settings
from django.utils.encoding import force_text
from django_redis import get_redis_connection
from temba.utils import dict_to_json


LOW_PRIORITY = +10000000   # +10M ~ 110 days
DEFAULT_PRIORITY = 0
HIGH_PRIORITY = -10000000  # -10M ~ 110 days
HIGHER_PRIORITY = -20000000  # -20M ~ 220 days

# for tasks using a redis lock to prevent overlapping this is the default timeout for the lock
DEFAULT_TASK_LOCK_TIMEOUT = 900


def push_task(org, queue, task_name, args, priority=DEFAULT_PRIORITY):
    """
    Adds a task to queue_name with the supplied arguments.

    Ex: push_task(nyaruka, 'flows', 'start_flow', [1,2,3,4,5,6,7,8,9,10])
    """
    r = get_redis_connection('default')

    # calculate our score from the current time and priority, this could get us in trouble
    # if things are queued for more than ~100 days, but otherwise gives us the properties of prioritizing
    # first based on priority, then insertion order.
    score = time.time() + priority

    # push our task onto the right queue and make sure it is in the active list (atomically)
    with r.pipeline() as pipe:
        org_id = org if isinstance(org, int) else org.id
        pipe.zadd("%s:%d" % (task_name, org_id), score, dict_to_json(args))

        # and make sure this key is in our list of queues so this job will get worked on
        pipe.zincrby("%s:active" % task_name, org_id, 0)
        pipe.execute()

    # if we were given a queue to schedule on, then add this task to celery.
    #
    # note that the task that is fired needs no arguments as it should just use pop_task with the
    # task name to determine what to work on.
    if queue:
        if getattr(settings, 'CELERY_ALWAYS_EAGER', False):
            task_function = lookup_task_function(task_name)
            task_function()
        else:  # pragma: needs cover
            current_app.send_task(task_name, args=[], kwargs={}, queue=queue)


def start_task(task_name):
    """
    Pops the next 'random' task off our queue, returning the arguments that were saved

    Ex: start_task('start_flow')
    <<< {flow=5, contacts=[1,2,3,4,5,6,7,8,9,10]}
    """
    r = get_redis_connection('default')

    task = None
    active_set = "%s:active" % task_name

    # get what queue we will work against, always the one with the lowest number of workers
    org_queue = r.zrange(active_set, 0, 0)

    while org_queue:
        # this lua script does both a "zpop" (popping the next highest thing off our sorted set) and
        # a clearing of our active set if there is no value in it as an atomic action
        lua = "local val = redis.call('zrange', ARGV[2], 0, 0) \n" \
              "if not next(val) then redis.call('zrem', ARGV[1], ARGV[3]) return nil \n"\
              "else redis.call('zincrby', ARGV[1], 1, ARGV[3]); redis.call('zremrangebyrank', ARGV[2], 0, 0) return val[1] end\n"

        task = r.eval(lua, 3, 'active_set', 'queue', 'org', active_set, '%s:%d' % (task_name, int(org_queue[0])), org_queue[0])

        # found a task? then break out
        if task is not None:
            task = json.loads(force_text(task))
            break

        # if we didn't get a task, then run again against a new queue until there is nothing left in our task queue
        org_queue = r.zrange(active_set, 0, 0)

    return int(org_queue[0]) if org_queue else None, task


def complete_task(task_name, org):
    """
    Marks the passed in task type as complete for the passed in organization.
    """
    r = get_redis_connection('default')
    active_set = "%s:active" % task_name
    key = "%d" % (org if isinstance(org, int) else org.id)

    lua = "local val = redis.call('zscore', ARGV[1], ARGV[2]) \n" \
          "if val then redis.call('zadd', ARGV[1], math.max(0, val-1), ARGV[2]) end \n"

    r.eval(lua, 2, 'active_set', 'queue', active_set, key)


def lookup_task_function(task_name):
    """
    Because Celery doesn't support using send_task() when ALWAYS_EAGER is on and we still want all our queue
    functionality to work in testing environments, we use a map in our settings to go from task name to function
    and call our tasks manually. This takes care of that.
    """
    task_map = getattr(settings, 'CELERY_TASK_MAP', None)
    if not task_map:  # pragma: needs cover
        print("Empty or missing CELERY_TASK_MAP in settings.py, unable to find task for %s" % task_name)

    task_function = task_map.get(task_name, None)
    if not task_function:  # pragma: needs cover
        raise Exception("Unable to find '%s' task in settings.CELERY_TASK_MAP, aborting" % task_name)

    m, f = task_function.rsplit('.', 1)
    mod = importlib.import_module(m)
    return getattr(mod, f)


def nonoverlapping_task(*task_args, **task_kwargs):
    """
    Decorator to create an task whose executions are prevented from overlapping by a redis lock
    """
    def _nonoverlapping_task(task_func):
        def wrapper(*exec_args, **exec_kwargs):
            r = get_redis_connection()

            task_name = task_kwargs.get('name', task_func.__name__)

            # lock key can be provided or defaults to celery-task-lock:<task_name>
            lock_key = task_kwargs.pop('lock_key', 'celery-task-lock:' + task_name)

            # lock timeout can be provided or defaults to task hard time limit
            lock_timeout = task_kwargs.pop('lock_timeout', None)
            if lock_timeout is None:
                lock_timeout = task_kwargs.get('time_limit', DEFAULT_TASK_LOCK_TIMEOUT)

            if r.get(lock_key):
                print("Skipping task %s to prevent overlapping" % task_name)
            else:
                with r.lock(lock_key, timeout=lock_timeout):
                    task_func(*exec_args, **exec_kwargs)

        return shared_task(*task_args, **task_kwargs)(wrapper)
    return _nonoverlapping_task
