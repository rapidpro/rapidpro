# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from redis.lock import Lock


class LockNotAcquiredException(Exception):
    pass


class NonBlockingLock(Lock):
    def __enter__(self):
        return self.acquire(blocking=False)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.release()
        elif exc_type is LockNotAcquiredException:
            # returning True means we handled the exception
            return True
        else:
            # all other exceptions are propagated
            return False
