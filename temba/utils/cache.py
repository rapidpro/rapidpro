from __future__ import unicode_literals

from redis_cache import get_redis_connection


def get_cacheable_result(cache_key, cache_ttl, callable, r=None):
    """
    Gets a cache-able calculation result
    """
    if not r:
        r = get_redis_connection()

    cached = r.get(cache_key)
    if cached is not None:
        try:
            return int(cached)
        except ValueError:
            pass

    calculated = int(callable())
    r.set(cache_key, calculated, cache_ttl)
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
