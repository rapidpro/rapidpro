import os
import redis

from django.conf import settings
from django.core.cache import cache

settings.configure()

key_prefix = cache.make_key('django_compressor')

redis_host = os.environ.get("REDIS_HOST", "localhost")
redis_port = int(os.environ.get("REDIS_PORT", 6379))
redis_password = os.environ.get("REDIS_PW", None)
redis_db = int(os.environ.get("REDIS_DB", 10))

if redis_password is None:
    redis = redis.Redis(host=redis_host, port=redis_port, db=redis_db)
else:
    redis = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password)

keys = redis.keys('%s.*' % (key_prefix,))
for key in keys:
    redis.delete(key)
    print('Cleared Django Compressor key: %s' % (key,))
