
from .settings import *  # noqa


# -----------------------------------------------------------------------------------
# Flowserver - on Travis we start a GoFlow instance at http://localhost:8800
# -----------------------------------------------------------------------------------
FLOW_SERVER_URL = 'http://localhost:8800'

DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME': 'temba',
        'USER': 'temba',
        'PASSWORD': 'temba',
        'HOST': 'localhost',
        'PORT': '',
        'ATOMIC_REQUESTS': True,
        'CONN_MAX_AGE': 60,
        'OPTIONS': {}
    }
}

# Use a fast hasher to speed up tests.
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]
