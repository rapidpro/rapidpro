import redis
import os
from django.conf import settings
from django.core.cache import cache

settings.configure()

key_prefix = cache.make_key('django_compressor')

REDIS_URL =  os.getenv('REDIS_PORT_6379_TCP_ADDR')

redis_host = REDIS_URL
redis_port =  6379
redis_password =  None
redis_db = 0

if redis_password is None:
  redis = redis.Redis(host=redis_host, port=redis_port, db=redis_db)
else:
  redis = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password)

keys = redis.keys('%s.*' % (key_prefix,))
for key in keys:
    redis.delete(key)
    print('Cleared Django Compressor key: %s' % (key,))
