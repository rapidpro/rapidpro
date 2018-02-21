# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json

from datetime import timedelta
from django.utils import timezone
from django.utils.encoding import force_text
from django_redis import get_redis_connection
from . import chunk_list


def get_cacheable(cache_key, callable, r=None, force_dirty=False):
    """
    Gets the result of a method call, using the given key and TTL as a cache
    """
    if not r:
        r = get_redis_connection()

    if not force_dirty:
        cached = r.get(cache_key)
        if cached is not None:
            return json.loads(force_text(cached))

    (calculated, cache_ttl) = callable()
    r.set(cache_key, json.dumps(calculated), cache_ttl)

    return calculated


def get_cacheable_result(cache_key, callable, r=None, force_dirty=False):
    """
    Gets a cache-able integer calculation result
    """
    return int(get_cacheable(cache_key, callable, r=r, force_dirty=force_dirty))


def get_cacheable_attr(obj, attr_name, calculate):
    """
    Gets the result of a method call, using the given object and attribute name
    as a cache
    """
    if hasattr(obj, attr_name):
        return getattr(obj, attr_name)

    calculated = calculate()
    setattr(obj, attr_name, calculated)

    return calculated


def incrby_existing(key, delta, r=None):
    """
    Update a existing integer value in the cache. If value doesn't exist, nothing happens. If value has a TTL, then that
    is preserved.
    """
    if not r:
        r = get_redis_connection()

    lua = "local ttl = redis.call('pttl', KEYS[1])\n" \
          "local val = redis.call('get', KEYS[1])\n" \
          "if val ~= false then\n" \
          "  val = tonumber(val) + ARGV[1]\n" \
          "  redis.call('set', KEYS[1], val)\n" \
          "  if ttl > 0 then\n" \
          "    redis.call('pexpire', KEYS[1], ttl)\n" \
          "  end\n" \
          "end"
    r.eval(lua, 1, key, delta)


class QueueRecord(object):
    """
    In several places we need to mark large numbers of items as queued. This utility class uses Redis sets to mark
    objects as queued, which is more efficient than having separate keys for each item. By having these expire after
    24 hours we ensure that our Redis sets can't grow indefinitely even if things fail.
    """
    def __init__(self, key_prefix, item_val=None):
        self.item_val = item_val or str

        key_format = key_prefix + '_%y_%m_%d'

        self.today_set_key = timezone.now().strftime(key_format)
        self.yesterday_set_key = (timezone.now() - timedelta(days=1)).strftime(key_format)

    def is_queued(self, item):
        item_value = self.item_val(item)

        # check whether we locked this item today or yesterday
        r = get_redis_connection()
        pipe = r.pipeline()
        pipe.sismember(self.today_set_key, item_value)
        pipe.sismember(self.yesterday_set_key, item_value)
        (queued_today, queued_yesterday) = pipe.execute()

        return queued_today or queued_yesterday

    def filter_unqueued(self, items):
        return [i for i in items if not self.is_queued(i)]

    def set_queued(self, items):
        """
        Marks the given items as queued
        """
        r = get_redis_connection()

        values = [self.item_val(i) for i in items]

        for value_batch in chunk_list(values, 50):
            pipe = r.pipeline()
            for v in value_batch:
                pipe.sadd(self.today_set_key, v)
            pipe.execute()

        r.expire(self.today_set_key, 86400)  # 24 hours
