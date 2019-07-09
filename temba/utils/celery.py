from functools import wraps

from django_redis import get_redis_connection

from celery import shared_task

# for tasks using a redis lock to prevent overlapping this is the default timeout for the lock
DEFAULT_TASK_LOCK_TIMEOUT = 900


def nonoverlapping_task(*task_args, **task_kwargs):
    """
    Decorator to create an task whose executions are prevented from overlapping by a redis lock
    """

    def _nonoverlapping_task(task_func):
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

            if r.get(lock_key):
                print("Skipping task %s to prevent overlapping" % task_name)
            else:
                with r.lock(lock_key, timeout=lock_timeout):
                    task_func(*exec_args, **exec_kwargs)

        return shared_task(*task_args, **task_kwargs)(wrapper)

    return _nonoverlapping_task
