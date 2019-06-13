from django_redis import get_redis_connection

from django.utils.encoding import force_text

from temba.utils import json


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

    lua = (
        "local ttl = redis.call('pttl', KEYS[1])\n"
        "local val = redis.call('get', KEYS[1])\n"
        "if val ~= false then\n"
        "  val = tonumber(val) + ARGV[1]\n"
        "  redis.call('set', KEYS[1], val)\n"
        "  if ttl > 0 then\n"
        "    redis.call('pexpire', KEYS[1], ttl)\n"
        "  end\n"
        "end"
    )
    r.eval(lua, 1, key, delta)
