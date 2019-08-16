from redis.lock import Lock


class LockNotAcquiredException(Exception):
    pass


class NonBlockingLock(Lock):
    """
    NonBlockingLock is a ContextManager for a Redis Lock that does not block when trying to acquire the lock. It also
    exposes the Lock within the ContextManager so it is possible to manipulate the lock directly and for example extend
    the duration of the lock with in context.

    Note:
        After requesting the lock, we MUST call the `exit_if_not_locked()` method that will do the right thing and
        check if the lock was actually successfully acquired.

    Common usage pattern:

        with NonBlockingLock(redis=get_redis_connection(), name=lock_key, timeout=3600) as lock:
            lock.exit_if_not_locked()
            .
            .
            lock.extend(additional_time=600)
            .
            .
    """

    acquired = False

    def __enter__(self):
        self.acquired = self.acquire(blocking=False)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.release()
        elif exc_type is LockNotAcquiredException:
            # returning True means we handled the exception
            return True
        else:
            # all other exceptions are propagated
            return False

    def exit_if_not_locked(self):
        if not self.acquired:
            raise LockNotAcquiredException
