from .settings import *  # noqa

# instead of running tests against temba_test whictest framework creates, run against regular temba database so that
# other components (i.e. mailroom) can be run against same database
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": "temba",
        "USER": "temba",
        "PASSWORD": "temba",
        "HOST": "localhost",
        "PORT": "5432",
        "ATOMIC_REQUESTS": True,
        "CONN_MAX_AGE": 60,
        "OPTIONS": {},
        "TEST": {"NAME": "temba"},  # use this same database for unit tests
    }
}

DATABASES["direct"] = DATABASES["default"]
