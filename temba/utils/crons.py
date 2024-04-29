import logging
from functools import wraps

from celery import shared_task
from django_redis import get_redis_connection

from django.utils import timezone

from . import analytics, json

logger = logging.getLogger(__name__)

# for tasks using a redis lock to prevent overlapping this is the default timeout for the lock
DEFAULT_TASK_LOCK_TIMEOUT = 900

STATS_EXPIRES = 60 * 60 * 48  # 2 days
STATS_KEY_BASE = "cron_stats"
STATS_LAST_START_KEY = f"{STATS_KEY_BASE}:last_start"
STATS_LAST_TIME_KEY = f"{STATS_KEY_BASE}:last_time"
STATS_LAST_RESULT_KEY = f"{STATS_KEY_BASE}:last_result"
STATS_CALL_COUNT_KEY = f"{STATS_KEY_BASE}:call_count"
STATS_TOTAL_TIME_KEY = f"{STATS_KEY_BASE}:total_time"
STATS_KEYS = (
    STATS_LAST_START_KEY,
    STATS_LAST_TIME_KEY,
    STATS_LAST_RESULT_KEY,
    STATS_CALL_COUNT_KEY,
    STATS_TOTAL_TIME_KEY,
)


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
            lock_key = "celery-task-lock:" + task_name

            # lock timeout can be provided or defaults to task hard time limit
            lock_timeout = task_kwargs.pop("lock_timeout", None)
            if lock_timeout is None:
                lock_timeout = task_kwargs.get("time_limit", DEFAULT_TASK_LOCK_TIMEOUT)

            start = timezone.now()
            result = None

            if r.get(lock_key):
                result = {"skipped": True}
            else:
                try:
                    with r.lock(lock_key, timeout=lock_timeout):
                        result = task_func(*exec_args, **exec_kwargs)
                finally:
                    _record_cron_execution(r, task_name, start, end=timezone.now(), result=result)

            return result

        return shared_task(*task_args, **task_kwargs)(wrapper)

    return _cron_task


def _record_cron_execution(r, name: str, start, end, result):
    pipe = r.pipeline()
    pipe.hset(STATS_LAST_START_KEY, name, start.isoformat())
    pipe.hset(STATS_LAST_TIME_KEY, name, str((end - start).total_seconds()))
    pipe.hset(STATS_LAST_RESULT_KEY, name, json.dumps(result))
    pipe.hincrby(STATS_CALL_COUNT_KEY, name, 1)
    pipe.hincrbyfloat(STATS_TOTAL_TIME_KEY, name, (end - start).total_seconds())

    for key in STATS_KEYS:
        pipe.expire(key, STATS_EXPIRES)

    pipe.execute()

    analytics.gauges({f"temba.cron_{name}": (end - start).total_seconds()})


def clear_cron_stats():
    r = get_redis_connection()
    for key in STATS_KEYS:
        r.delete(key)
