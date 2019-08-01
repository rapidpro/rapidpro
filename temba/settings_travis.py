from .settings import *  # noqa

# -----------------------------------------------------------------------------------
# Mailroom - on Travis we start an instance at http://localhost:8090
# -----------------------------------------------------------------------------------
MAILROOM_URL = "http://localhost:8090"

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

# Use a fast hasher to speed up tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
