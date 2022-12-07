import logging
from functools import wraps

from celery import shared_task
from django_redis import get_redis_connection

from django.utils import timezone

# for tasks using a redis lock to prevent overlapping this is the default timeout for the lock
DEFAULT_TASK_LOCK_TIMEOUT = 900

logger = logging.getLogger(__name__)


def cron_task(*task_args, **task_kwargs):
    """
    Decorator to create an task suitable for a cron schedule, whose executions are prevented from overlapping by a
    redis lock
    """

    def _cron_task(task_func):
        @wraps(task_func)
        def wrapper(*exec_args, **exec_kwargs):
            r = get_redis_connection()

            task_name = task_kwargs.get("name", task_func.__name__)

            # lock key can be provided or defaults to celery-task-lock:<task_name>
            lock_key = task_kwargs.pop("lock_key", "celery-task-lock:" + task_name)

            # lock timeout can be provided or defaults to task hard time limit
            lock_timeout = task_kwargs.pop("lock_timeout", None)
            if lock_timeout is None:
                lock_timeout = task_kwargs.get("time_limit", DEFAULT_TASK_LOCK_TIMEOUT)

            start = timezone.now()

            if r.get(lock_key):
                result = {"skipped": True}
            else:
                try:
                    with r.lock(lock_key, timeout=lock_timeout):
                        result = task_func(*exec_args, **exec_kwargs)
                finally:
                    r.hset("cron_times", task_name, (timezone.now() - start).total_seconds)

            return result

        return shared_task(*task_args, **task_kwargs)(wrapper)

    return _cron_task
