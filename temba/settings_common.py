import os
import sys
from datetime import timedelta

import iptools
from celery.schedules import crontab

from django.utils.translation import gettext_lazy as _

# -----------------------------------------------------------------------------------
# Default to debugging
# -----------------------------------------------------------------------------------
DEBUG = True

# -----------------------------------------------------------------------------------
# Sets TESTING to True if this configuration is read during a unit test
# -----------------------------------------------------------------------------------
TESTING = sys.argv[1:2] == ["test"]

if TESTING:
    PASSWORD_HASHERS = ("django.contrib.auth.hashers.MD5PasswordHasher",)
    DEBUG = False

ADMINS = (("RapidPro", "code@yourdomain.io"),)
MANAGERS = ADMINS

USE_DEPRECATED_PYTZ = True

# -----------------------------------------------------------------------------------
# set the mail settings, override these in your settings.py
# if your site was at http://temba.io, it might look like this:
# -----------------------------------------------------------------------------------
EMAIL_HOST = "smtp.gmail.com"
EMAIL_HOST_USER = "server@temba.io"
DEFAULT_FROM_EMAIL = "server@temba.io"
EMAIL_HOST_PASSWORD = "mypassword"
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 10

# Used when sending email from within a flow and the user hasn't configured
# their own SMTP server.
FLOW_FROM_EMAIL = "Temba <no-reply@temba.io>"

# HTTP Headers using for outgoing requests to other services
OUTGOING_REQUEST_HEADERS = {"User-agent": "RapidPro"}

STORAGE_URL = None  # may be an absolute URL to /media (like http://localhost:8000/media) or AWS S3
STORAGE_ROOT_DIR = "test_orgs" if TESTING else "orgs"

# -----------------------------------------------------------------------------------
# AWS S3 storage used in production
# -----------------------------------------------------------------------------------
AWS_ACCESS_KEY_ID = "aws_access_key_id"
AWS_SECRET_ACCESS_KEY = "aws_secret_access_key"
AWS_DEFAULT_ACL = "private"

AWS_STORAGE_BUCKET_NAME = "dl-temba-io"
AWS_BUCKET_DOMAIN = AWS_STORAGE_BUCKET_NAME + ".s3.amazonaws.com"

# bucket where archives files are stored
ARCHIVE_BUCKET = "dl-temba-archives"

# -----------------------------------------------------------------------------------
# On Unix systems, a value of None will cause Django to use the same
# timezone as the operating system.
# If running in a Windows environment this must be set to the same as your
# system time zone
# -----------------------------------------------------------------------------------
USE_TZ = True
TIME_ZONE = "GMT"
USER_TIME_ZONE = "Africa/Kigali"

# -----------------------------------------------------------------------------------
# Default language used for this installation
# -----------------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"

# -----------------------------------------------------------------------------------
# Available languages for translation
# -----------------------------------------------------------------------------------
LANGUAGES = (
    ("en-us", _("English")),
    ("cs", _("Czech")),
    ("es", _("Spanish")),
    ("fr", _("French")),
    ("mn", _("Mongolian")),
    ("pt-br", _("Portuguese")),
    ("ru", _("Russian")),
)
DEFAULT_LANGUAGE = "en-us"

SITE_ID = 1

# If you set this to False, Django will make some optimizations so as not
# to load the internationalization machinery.
USE_I18N = True

# If you set this to False, Django will not format dates, numbers and
# calendars according to the current locale
USE_L10N = True

# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "compressor.finders.CompressorFinder",
)

# Make this unique, and don't share it with anybody.
SECRET_KEY = "your own secret key"

# -----------------------------------------------------------------------------------
# Directory Configuration
# -----------------------------------------------------------------------------------
PROJECT_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)))
LOCALE_PATHS = (os.path.join(PROJECT_DIR, "../locale"),)
RESOURCES_DIR = os.path.join(PROJECT_DIR, "../resources")
FIXTURE_DIRS = (os.path.join(PROJECT_DIR, "../fixtures"),)
TESTFILES_DIR = os.path.join(PROJECT_DIR, "../testfiles")
STATICFILES_DIRS = (
    os.path.join(PROJECT_DIR, "../static"),
    os.path.join(PROJECT_DIR, "../media"),
    os.path.join(PROJECT_DIR, "../node_modules/@nyaruka/flow-editor/build"),
    os.path.join(PROJECT_DIR, "../node_modules/@nyaruka/temba-components/dist/static"),
    os.path.join(PROJECT_DIR, "../node_modules"),
    os.path.join(PROJECT_DIR, "../node_modules/react/umd"),
    os.path.join(PROJECT_DIR, "../node_modules/react-dom/umd"),
)
STATIC_ROOT = os.path.join(PROJECT_DIR, "../sitestatic")
STATIC_URL = "/sitestatic/"
COMPRESS_ROOT = os.path.join(PROJECT_DIR, "../sitestatic")
MEDIA_ROOT = os.path.join(PROJECT_DIR, "../media")
MEDIA_URL = "/media/"

HELP_URL = None


# -----------------------------------------------------------------------------------
# Templates Configuration
# -----------------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            os.path.join(PROJECT_DIR, "../templates"),
            os.path.join(PROJECT_DIR, "../node_modules/@nyaruka/temba-components/dist/templates"),
        ],
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.debug",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.template.context_processors.static",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.request",
                "temba.context_processors.branding",
                "temba.context_processors.config",
                "temba.orgs.context_processors.user_group_perms_processor",
                "temba.orgs.context_processors.user_orgs",
            ],
            "loaders": [
                "temba.utils.haml.HamlFilesystemLoader",
                "temba.utils.haml.HamlAppDirectoriesLoader",
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
            "debug": False if TESTING else DEBUG,
        },
    }
]

if TESTING:
    TEMPLATES[0]["OPTIONS"]["context_processors"] += ("temba.tests.add_testing_flag_to_context",)

FORM_RENDERER = "django.forms.renderers.TemplatesSetting"

MIDDLEWARE = (
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "temba.middleware.OrgMiddleware",
    "temba.middleware.BrandingMiddleware",
    "temba.middleware.LanguageMiddleware",
    "temba.middleware.TimezoneMiddleware",
)

ROOT_URLCONF = "temba.urls"

# other urls to add
APP_URLS = []

SITEMAP = ("public.public_index", "public.public_blog", "public.video_list", "api")

INSTALLED_APPS = (
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.gis",
    "django.contrib.sitemaps",
    "django.contrib.postgres",
    "django.forms",
    "formtools",
    # Haml-like templates
    "hamlpy",
    # Redis cache
    "redis",
    # rest framework for api access
    "rest_framework",
    "rest_framework.authtoken",
    # compress our CSS and js
    "compressor",
    # smartmin
    "smartmin",
    "smartmin.csv_imports",
    "smartmin.users",
    # django-timezone-field
    "timezone_field",
    # temba apps
    "temba.apks",
    "temba.archives",
    "temba.assets",
    "temba.auth_tweaks",
    "temba.api",
    "temba.request_logs",
    "temba.classifiers",
    "temba.dashboard",
    "temba.globals",
    "temba.public",
    "temba.schedules",
    "temba.templates",
    "temba.orgs",
    "temba.contacts",
    "temba.channels",
    "temba.msgs",
    "temba.notifications",
    "temba.flows",
    "temba.tickets",
    "temba.triggers",
    "temba.utils",
    "temba.campaigns",
    "temba.ivr",
    "temba.locations",
    "temba.airtime",
    "temba.sql",
)

# the last installed app that uses smartmin permissions
PERMISSIONS_APP = "temba.airtime"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "formatters": {"verbose": {"format": "%(levelname)s %(asctime)s %(module)s %(message)s"}},
    "handlers": {
        "console": {"level": "DEBUG", "class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"level": "INFO", "handlers": ["console"]},
}

# -----------------------------------------------------------------------------------
# Branding Configuration
# -----------------------------------------------------------------------------------
BRAND = {
    "slug": "rapidpro",
    "name": "RapidPro",
    "hosts": ["rapidpro.io"],
    "org": "UNICEF",
    "domain": "app.rapidpro.io",
    "styles": [],
    "email": "join@rapidpro.io",
    "support_email": "support@rapidpro.io",
    "link": "https://app.rapidpro.io",
    "docs_link": "http://docs.rapidpro.io",
    "ticket_domain": "tickets.rapidpro.io",
    "favico": "brands/rapidpro/rapidpro.ico",
    "splash": "brands/rapidpro/splash.jpg",
    "logo": "images/logo-dark.svg",
    "allow_signups": True,
    "title": _("Visually build nationally scalable mobile applications"),
}

FEATURES = {"locations", "surveyor", "ticketers"}


# -----------------------------------------------------------------------------------
# Permission Management
# -----------------------------------------------------------------------------------

# this lets us easily create new permissions across our objects
PERMISSIONS = {
    "*": (
        "create",  # can create an object
        "read",  # can read an object, viewing it's details
        "update",  # can update an object
        "delete",  # can delete an object,
        "list",  # can view a list of the objects
    ),
    "api.apitoken": ("refresh",),
    "api.resthook": ("api", "list"),
    "api.webhookevent": ("api",),
    "api.resthooksubscriber": ("api",),
    "archives.archive": ("api", "run", "message"),
    "campaigns.campaign": ("api", "archived", "archive", "activate", "menu"),
    "campaigns.campaignevent": ("api",),
    "channels.channel": (
        "api",
        "claim",
        "configuration",
        "errors",
        "facebook_whitelist",
        "menu",
    ),
    "channels.channellog": ("connection",),
    "channels.channelevent": ("api",),
    "classifiers.classifier": ("connect", "api", "sync", "menu"),
    "classifiers.intent": ("api",),
    "contacts.contact": (
        "api",
        "export",
        "history",
        "interrupt",
        "menu",
        "omnibox",
        "open_ticket",
        "start",
    ),
    "contacts.contactfield": ("api", "menu", "update_priority", "featured", "filter_by_type"),
    "contacts.contactgroup": ("api", "menu"),
    "contacts.contactimport": ("preview",),
    "flows.flowstart": ("api",),
    "flows.flow": (
        "activity_chart",
        "activity_list",
        "activity",
        "api",
        "archived",
        "assets",
        "broadcast",
        "category_counts",
        "change_language",
        "copy",
        "download_translation",
        "editor",
        "export_results",
        "export_translation",
        "export",
        "filter",
        "import_translation",
        "menu",
        "recent_contacts",
        "results",
        "revisions",
        "simulate",
    ),
    "flows.flowsession": ("json",),
    "globals.global": ("api", "unused"),
    "ivr.call": ("list",),
    "locations.adminboundary": ("alias", "api", "boundaries", "geometry"),
    "msgs.broadcast": (
        "api",
        "scheduled",
        "scheduled_create",
        "scheduled_read",
        "scheduled_update",
        "scheduled_delete",
        "send",
    ),
    "msgs.label": ("api",),
    "msgs.media": ("upload", "list", "api"),
    "msgs.msg": (
        "api",
        "archive",
        "export",
        "label",
        "menu",
        "update",
    ),
    "orgs.org": (
        "account",
        "api",
        "country",
        "create_login",
        "create",
        "dashboard",
        "download",
        "edit_sub_org",
        "edit",
        "export",
        "grant",
        "import",
        "join_accept",
        "join",
        "languages",
        "manage_accounts_sub_org",
        "manage_accounts",
        "manage_integrations",
        "manage",
        "menu",
        "profile",
        "prometheus",
        "resthooks",
        "service",
        "signup",
        "smtp_server",
        "spa",
        "sub_orgs",
        "surveyor",
        "token",
        "trial",
        "twilio_account",
        "twilio_connect",
        "two_factor",
        "workspace",
    ),
    "request_logs.httplog": ("webhooks", "classifier", "ticketer"),
    "templates.template": ("api",),
    "tickets.ticket": ("api", "assign", "assignee", "menu", "note", "export_stats", "export"),
    "tickets.ticketer": ("api", "connect", "configure"),
    "tickets.topic": ("api",),
    "triggers.trigger": ("archived", "type", "menu"),
}


# assigns the permissions that each group should have
GROUP_PERMISSIONS = {
    "Service Users": ("flows.flow_assets", "msgs.msg_create"),  # internal Temba services have limited permissions
    "Alpha": (),
    "Beta": (),
    "Dashboard": ("orgs.org_dashboard",),
    "Surveyors": (
        "contacts.contact_api",
        "contacts.contactfield_api",
        "contacts.contactgroup_api",
        "flows.flow_api",
        "locations.adminboundary_api",
        "msgs.msg_api",
        "orgs.org_api",
        "orgs.org_surveyor",
    ),
    "Customer Support": (),
    "Granters": ("orgs.org_grant",),
    "Administrators": (
        "airtime.airtimetransfer_list",
        "airtime.airtimetransfer_read",
        "api.apitoken_refresh",
        "api.resthook_api",
        "api.resthooksubscriber_api",
        "api.webhookevent_api",
        "archives.archive.*",
        "campaigns.campaign.*",
        "campaigns.campaignevent.*",
        "channels.channel_api",
        "channels.channel_claim",
        "channels.channel_configuration",
        "channels.channel_create",
        "channels.channel_delete",
        "channels.channel_facebook_whitelist",
        "channels.channel_list",
        "channels.channel_menu",
        "channels.channel_read",
        "channels.channel_update",
        "channels.channelevent.*",
        "channels.channellog_list",
        "channels.channellog_read",
        "classifiers.classifier_api",
        "classifiers.classifier_connect",
        "classifiers.classifier_delete",
        "classifiers.classifier_list",
        "classifiers.classifier_menu",
        "classifiers.classifier_read",
        "classifiers.classifier_sync",
        "classifiers.intent_api",
        "contacts.contact_api",
        "contacts.contact_create",
        "contacts.contact_delete",
        "contacts.contact_export",
        "contacts.contact_history",
        "contacts.contact_interrupt",
        "contacts.contact_list",
        "contacts.contact_menu",
        "contacts.contact_omnibox",
        "contacts.contact_open_ticket",
        "contacts.contact_read",
        "contacts.contact_update",
        "contacts.contactfield.*",
        "contacts.contactgroup.*",
        "contacts.contactimport.*",
        "csv_imports.importtask.*",
        "flows.flow.*",
        "flows.flowlabel.*",
        "flows.flowrun_delete",
        "flows.flowstart.*",
        "globals.global.*",
        "ivr.call.*",
        "locations.adminboundary_alias",
        "locations.adminboundary_api",
        "locations.adminboundary_boundaries",
        "locations.adminboundary_geometry",
        "msgs.broadcast.*",
        "msgs.label.*",
        "msgs.media_api",
        "msgs.media_upload",
        "msgs.msg_api",
        "msgs.msg_archive",
        "msgs.msg_delete",
        "msgs.msg_export",
        "msgs.msg_label",
        "msgs.msg_list",
        "msgs.msg_menu",
        "msgs.msg_update",
        "notifications.incident.*",
        "notifications.notification.*",
        "orgs.org_account",
        "orgs.org_api",
        "orgs.org_country",
        "orgs.org_create",
        "orgs.org_dashboard",
        "orgs.org_delete",
        "orgs.org_download",
        "orgs.org_edit_sub_org",
        "orgs.org_edit",
        "orgs.org_export",
        "orgs.org_import",
        "orgs.org_languages",
        "orgs.org_manage_accounts_sub_org",
        "orgs.org_manage_accounts",
        "orgs.org_manage_integrations",
        "orgs.org_menu",
        "orgs.org_profile",
        "orgs.org_prometheus",
        "orgs.org_resthooks",
        "orgs.org_smtp_server",
        "orgs.org_sub_orgs",
        "orgs.org_token",
        "orgs.org_two_factor",
        "orgs.org_workspace",
        "request_logs.httplog_list",
        "request_logs.httplog_read",
        "request_logs.httplog_webhooks",
        "schedules.schedule.*",
        "templates.template_api",
        "tickets.ticket.*",
        "tickets.ticketer.*",
        "tickets.topic.*",
        "triggers.trigger.*",
    ),
    "Editors": (
        "airtime.airtimetransfer_list",
        "airtime.airtimetransfer_read",
        "api.apitoken_refresh",
        "api.resthook_api",
        "api.resthooksubscriber_api",
        "api.webhookevent_api",
        "api.webhookevent_list",
        "api.webhookevent_read",
        "archives.archive.*",
        "campaigns.campaign.*",
        "campaigns.campaignevent.*",
        "channels.channel_api",
        "channels.channel_claim",
        "channels.channel_configuration",
        "channels.channel_create",
        "channels.channel_delete",
        "channels.channel_list",
        "channels.channel_menu",
        "channels.channel_read",
        "channels.channel_update",
        "channels.channelevent.*",
        "classifiers.classifier_api",
        "classifiers.classifier_list",
        "classifiers.classifier_menu",
        "classifiers.classifier_read",
        "classifiers.intent_api",
        "contacts.contact_api",
        "contacts.contact_create",
        "contacts.contact_delete",
        "contacts.contact_export",
        "contacts.contact_history",
        "contacts.contact_interrupt",
        "contacts.contact_list",
        "contacts.contact_menu",
        "contacts.contact_omnibox",
        "contacts.contact_open_ticket",
        "contacts.contact_read",
        "contacts.contact_update",
        "contacts.contactfield.*",
        "contacts.contactgroup.*",
        "contacts.contactimport.*",
        "csv_imports.importtask.*",
        "flows.flow.*",
        "flows.flowlabel.*",
        "flows.flowrun_delete",
        "flows.flowstart_api",
        "flows.flowstart_list",
        "globals.global_api",
        "ivr.call_list",
        "locations.adminboundary_alias",
        "locations.adminboundary_api",
        "locations.adminboundary_boundaries",
        "locations.adminboundary_geometry",
        "msgs.broadcast.*",
        "msgs.label.*",
        "msgs.media_api",
        "msgs.media_upload",
        "msgs.msg_api",
        "msgs.msg_archive",
        "msgs.msg_delete",
        "msgs.msg_export",
        "msgs.msg_label",
        "msgs.msg_list",
        "msgs.msg_menu",
        "msgs.msg_update",
        "notifications.notification_list",
        "orgs.org_account",
        "orgs.org_api",
        "orgs.org_download",
        "orgs.org_export",
        "orgs.org_import",
        "orgs.org_languages",
        "orgs.org_menu",
        "orgs.org_profile",
        "orgs.org_resthooks",
        "orgs.org_token",
        "orgs.org_two_factor",
        "orgs.org_workspace",
        "request_logs.httplog_webhooks",
        "schedules.schedule.*",
        "templates.template_api",
        "tickets.ticket.*",
        "tickets.ticketer_api",
        "tickets.topic.*",
        "triggers.trigger.*",
    ),
    "Viewers": (
        "campaigns.campaign_archived",
        "campaigns.campaign_list",
        "campaigns.campaign_menu",
        "campaigns.campaign_read",
        "campaigns.campaignevent_read",
        "channels.channel_list",
        "channels.channel_menu",
        "channels.channel_read",
        "classifiers.classifier_api",
        "classifiers.classifier_list",
        "classifiers.classifier_menu",
        "classifiers.classifier_read",
        "classifiers.intent_api",
        "contacts.contact_export",
        "contacts.contact_history",
        "contacts.contact_list",
        "contacts.contact_menu",
        "contacts.contact_read",
        "contacts.contactfield_api",
        "contacts.contactfield_read",
        "contacts.contactgroup_api",
        "contacts.contactgroup_menu",
        "contacts.contactgroup_read",
        "contacts.contactimport_read",
        "flows.flow_activity_chart",
        "flows.flow_activity",
        "flows.flow_archived",
        "flows.flow_assets",
        "flows.flow_category_counts",
        "flows.flow_editor",
        "flows.flow_export_results",
        "flows.flow_export",
        "flows.flow_filter",
        "flows.flow_list",
        "flows.flow_menu",
        "flows.flow_recent_contacts",
        "flows.flow_results",
        "flows.flow_revisions",
        "flows.flow_simulate",
        "flows.flowstart_list",
        "globals.global_api",
        "ivr.call_list",
        "locations.adminboundary_alias",
        "locations.adminboundary_boundaries",
        "locations.adminboundary_geometry",
        "msgs.broadcast_scheduled_read",
        "msgs.label_api",
        "msgs.label_read",
        "msgs.msg_export",
        "msgs.msg_list",
        "msgs.msg_menu",
        "notifications.notification_list",
        "orgs.org_account",
        "orgs.org_api",
        "orgs.org_download",
        "orgs.org_export",
        "orgs.org_menu",
        "orgs.org_menu",
        "orgs.org_profile",
        "orgs.org_two_factor",
        "orgs.org_workspace",
        "tickets.ticketer_api",
        "tickets.topic_api",
        "tickets.ticket_export",
        "triggers.trigger_archived",
        "triggers.trigger_list",
        "triggers.trigger_menu",
        "triggers.trigger_type",
    ),
    "Agents": (
        "contacts.contact_api",
        "contacts.contact_history",
        "contacts.contactfield_api",
        "contacts.contactgroup_api",
        "globals.global_api",
        "msgs.media_api",
        "msgs.msg_api",
        "msgs.broadcast_api",
        "notifications.notification_list",
        "orgs.org_account",
        "orgs.org_api",
        "orgs.org_languages",
        "orgs.org_menu",
        "orgs.org_profile",
        "orgs.org_two_factor",
        "tickets.ticket_api",
        "tickets.ticket_assign",
        "tickets.ticket_assignee",
        "tickets.ticket_list",
        "tickets.ticket_menu",
        "tickets.ticket_note",
        "tickets.topic_api",
    ),
    "Prometheus": (),
}

# -----------------------------------------------------------------------------------
# Login / Logout
# -----------------------------------------------------------------------------------
LOGIN_URL = "/users/login/"
LOGOUT_URL = "/users/logout/"
LOGIN_REDIRECT_URL = "/org/choose/"
LOGOUT_REDIRECT_URL = "/"

AUTHENTICATION_BACKENDS = ("temba.orgs.backend.AuthenticationBackend",)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
]

ANONYMOUS_USER_NAME = "AnonymousUser"

# -----------------------------------------------------------------------------------
# Our test runner includes the ability to exclude apps
# -----------------------------------------------------------------------------------
TEST_RUNNER = "temba.tests.runner.TembaTestRunner"
TEST_EXCLUDE = ("smartmin",)

# -----------------------------------------------------------------------------------
# Need a PostgreSQL database on localhost with postgis extension installed.
# -----------------------------------------------------------------------------------
_default_database_config = {
    "ENGINE": "django.contrib.gis.db.backends.postgis",
    "NAME": "temba",
    "USER": "temba",
    "PASSWORD": "temba",
    "HOST": "localhost",
    "PORT": "5432",
    "ATOMIC_REQUESTS": True,
    "CONN_MAX_AGE": 60,
    "OPTIONS": {},
    "DISABLE_SERVER_SIDE_CURSORS": True,
}

# installs can provide a default connection and an optional read-only connection (e.g. a separate read replica) which
# will be used for certain fetch operations
DATABASES = {"default": _default_database_config, "readonly": _default_database_config.copy()}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

INTERNAL_IPS = iptools.IpRangeList("127.0.0.1", "192.168.0.10", "192.168.0.0/24", "0.0.0.0")  # network block

HOSTNAME = "localhost"

# The URL and port of the proxy server to use when needed (if any, in requests format)
OUTGOING_PROXIES = {}

# -----------------------------------------------------------------------------------
# Caching using Redis
# -----------------------------------------------------------------------------------
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 10 if TESTING else 15  # we use a redis db of 10 for testing so that we maintain caches for dev

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://%s:%s/%s" % (REDIS_HOST, REDIS_PORT, REDIS_DB),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

# -----------------------------------------------------------------------------------
# Async tasks using Celery
# -----------------------------------------------------------------------------------
CELERY_RESULT_BACKEND = None
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_URL = "redis://%s:%d/%d" % (REDIS_HOST, REDIS_PORT, REDIS_DB)

# by default, celery doesn't have any timeout on our redis connections, this fixes that
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_timeout": 5}

CELERY_BEAT_SCHEDULE = {
    "check-channel-alerts": {"task": "check_channel_alerts", "schedule": timedelta(seconds=300)},
    "check-elasticsearch-lag": {"task": "check_elasticsearch_lag", "schedule": timedelta(seconds=300)},
    "delete-released-orgs": {"task": "delete_released_orgs", "schedule": crontab(hour=4, minute=0)},
    "fail-old-messages": {"task": "fail_old_messages", "schedule": crontab(hour=0, minute=0)},
    "interrupt-flow-sessions": {"task": "interrupt_flow_sessions", "schedule": crontab(hour=23, minute=30)},
    "resolve-twitter-ids": {"task": "resolve_twitter_ids", "schedule": timedelta(seconds=900)},
    "refresh-whatsapp-tokens": {"task": "refresh_whatsapp_tokens", "schedule": crontab(hour=6, minute=0)},
    "refresh-whatsapp-templates": {"task": "refresh_whatsapp_templates", "schedule": timedelta(seconds=900)},
    "send-notification-emails": {"task": "send_notification_emails", "schedule": timedelta(seconds=60)},
    "squash-channel-counts": {"task": "squash_channel_counts", "schedule": timedelta(seconds=60)},
    "squash-group-counts": {"task": "squash_group_counts", "schedule": timedelta(seconds=60)},
    "squash-flow-counts": {"task": "squash_flow_counts", "schedule": timedelta(seconds=60)},
    "squash-msg-counts": {"task": "squash_msg_counts", "schedule": timedelta(seconds=60)},
    "squash-notification-counts": {"task": "squash_notification_counts", "schedule": timedelta(seconds=60)},
    "squash-ticket-counts": {"task": "squash_ticket_counts", "schedule": timedelta(seconds=60)},
    "sync-classifier-intents": {"task": "sync_classifier_intents", "schedule": timedelta(seconds=300)},
    "sync-old-seen-channels": {"task": "sync_old_seen_channels", "schedule": timedelta(seconds=600)},
    "track-org-channel-counts": {"task": "track_org_channel_counts", "schedule": crontab(hour=4, minute=0)},
    "trim-channel-logs": {"task": "trim_channel_logs", "schedule": crontab(hour=3, minute=0)},
    "trim-event-fires": {"task": "trim_event_fires", "schedule": timedelta(seconds=900)},
    "trim-flow-revisions": {"task": "trim_flow_revisions", "schedule": crontab(hour=0, minute=0)},
    "trim-flow-sessions": {"task": "trim_flow_sessions", "schedule": crontab(hour=0, minute=0)},
    "trim-http-logs": {"task": "trim_http_logs", "schedule": crontab(hour=2, minute=0)},
    "trim-sync-events": {"task": "trim_sync_events", "schedule": crontab(hour=3, minute=0)},
    "trim-webhook-events": {"task": "trim_webhook_events", "schedule": crontab(hour=3, minute=0)},
}

# -----------------------------------------------------------------------------------
# Django-rest-framework configuration
# -----------------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_AUTHENTICATION_CLASSES": ("temba.api.support.APISessionAuthentication",),
    "DEFAULT_THROTTLE_RATES": {
        "v2": "2500/hour",
        "v2.contacts": "2500/hour",
        "v2.messages": "2500/hour",
        "v2.broadcasts": "36000/hour",
        "v2.runs": "2500/hour",
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "DEFAULT_RENDERER_CLASSES": ("temba.api.support.DocumentationRenderer", "rest_framework.renderers.JSONRenderer"),
    "PAGE_SIZE": 250,
    "EXCEPTION_HANDLER": "temba.api.support.temba_exception_handler",
}
REST_HANDLE_EXCEPTIONS = not TESTING

# -----------------------------------------------------------------------------------
# Django Compressor configuration
# -----------------------------------------------------------------------------------

if TESTING:
    # if only testing, disable less compilation
    COMPRESS_PRECOMPILERS = ()
else:
    COMPRESS_PRECOMPILERS = (
        ("text/less", 'lessc --include-path="%s" {infile} {outfile}' % os.path.join(PROJECT_DIR, "../static", "less")),
    )

COMPRESS_ENABLED = False
COMPRESS_OFFLINE = False

# -----------------------------------------------------------------------------------
# RapidPro configuration settings
# -----------------------------------------------------------------------------------

######
# DANGER: only turn this on if you know what you are doing!
#         could cause emails to be sent in test environment
SEND_EMAILS = False

INTEGRATION_TYPES = [
    "temba.orgs.integrations.dtone.DTOneType",
]

CLASSIFIER_TYPES = [
    "temba.classifiers.types.wit.WitType",
    "temba.classifiers.types.luis.LuisType",
    "temba.classifiers.types.bothub.BothubType",
]

TICKETER_TYPES = [
    "temba.tickets.types.internal.InternalType",
    "temba.tickets.types.mailgun.MailgunType",
    "temba.tickets.types.zendesk.ZendeskType",
    "temba.tickets.types.rocketchat.RocketChatType",
]

CHANNEL_TYPES = [
    "temba.channels.types.africastalking.AfricasTalkingType",
    "temba.channels.types.arabiacell.ArabiaCellType",
    "temba.channels.types.bandwidth.BandwidthType",
    "temba.channels.types.blackmyna.BlackmynaType",
    "temba.channels.types.bongolive.BongoLiveType",
    "temba.channels.types.burstsms.BurstSMSType",
    "temba.channels.types.chikka.ChikkaType",
    "temba.channels.types.clickatell.ClickatellType",
    "temba.channels.types.clickmobile.ClickMobileType",
    "temba.channels.types.clicksend.ClickSendType",
    "temba.channels.types.dartmedia.DartMediaType",
    "temba.channels.types.dialog360.Dialog360Type",
    "temba.channels.types.discord.DiscordType",
    "temba.channels.types.dmark.DMarkType",
    "temba.channels.types.external.ExternalType",
    "temba.channels.types.facebook.FacebookType",
    "temba.channels.types.facebookapp.FacebookAppType",
    "temba.channels.types.firebase.FirebaseCloudMessagingType",
    "temba.channels.types.freshchat.FreshChatType",
    "temba.channels.types.globe.GlobeType",
    "temba.channels.types.highconnection.HighConnectionType",
    "temba.channels.types.hormuud.HormuudType",
    "temba.channels.types.hub9.Hub9Type",
    "temba.channels.types.i2sms.I2SMSType",
    "temba.channels.types.infobip.InfobipType",
    "temba.channels.types.instagram.InstagramType",
    "temba.channels.types.jasmin.JasminType",
    "temba.channels.types.jiochat.JioChatType",
    "temba.channels.types.junebug.JunebugType",
    "temba.channels.types.justcall.JustCallType",
    "temba.channels.types.kaleyra.KaleyraType",
    "temba.channels.types.kannel.KannelType",
    "temba.channels.types.line.LineType",
    "temba.channels.types.m3tech.M3TechType",
    "temba.channels.types.macrokiosk.MacrokioskType",
    "temba.channels.types.mblox.MbloxType",
    "temba.channels.types.messangi.MessangiType",
    "temba.channels.types.mtn.MtnType",
    "temba.channels.types.mtarget.MtargetType",
    "temba.channels.types.novo.NovoType",
    "temba.channels.types.playmobile.PlayMobileType",
    "temba.channels.types.plivo.PlivoType",
    "temba.channels.types.redrabbit.RedRabbitType",
    "temba.channels.types.rocketchat.RocketChatType",
    "temba.channels.types.shaqodoon.ShaqodoonType",
    "temba.channels.types.signalwire.SignalWireType",
    "temba.channels.types.slack.SlackType",
    "temba.channels.types.smscentral.SMSCentralType",
    "temba.channels.types.somleng.SomlengType",
    "temba.channels.types.start.StartType",
    "temba.channels.types.telegram.TelegramType",
    "temba.channels.types.telesom.TelesomType",
    "temba.channels.types.thinq.ThinQType",
    "temba.channels.types.twilio_messaging_service.TwilioMessagingServiceType",
    "temba.channels.types.twilio_whatsapp.TwilioWhatsappType",
    "temba.channels.types.twilio.TwilioType",
    "temba.channels.types.twitter_legacy.TwitterLegacyType",
    "temba.channels.types.twitter.TwitterType",
    "temba.channels.types.verboice.VerboiceType",
    "temba.channels.types.viber_public.ViberPublicType",
    "temba.channels.types.vk.VKType",
    "temba.channels.types.vonage.VonageType",
    "temba.channels.types.wavy.WavyType",
    "temba.channels.types.wechat.WeChatType",
    "temba.channels.types.whatsapp_cloud.WhatsAppCloudType",
    "temba.channels.types.whatsapp.WhatsAppType",
    "temba.channels.types.yo.YoType",
    "temba.channels.types.zenvia_sms.ZenviaSMSType",
    "temba.channels.types.zenvia_whatsapp.ZenviaWhatsAppType",
    "temba.channels.types.zenvia.ZenviaType",
    "temba.channels.types.android.AndroidType",
]

ANALYTICS_TYPES = [
    "temba.utils.analytics.ConsoleBackend",
]

# set of ISO-639-3 codes of languages to allow in addition to all ISO-639-1 languages
NON_ISO6391_LANGUAGES = {"mul", "und"}

# -----------------------------------------------------------------------------------
# Store sessions in our cache
# -----------------------------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_CACHE_ALIAS = "default"

# -----------------------------------------------------------------------------------
# 3rd Party Integration Keys
# -----------------------------------------------------------------------------------
TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY", "MISSING_TWITTER_API_KEY")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET", "MISSING_TWITTER_API_SECRET")

MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY", "")

ZENDESK_CLIENT_ID = os.environ.get("ZENDESK_CLIENT_ID", "")
ZENDESK_CLIENT_SECRET = os.environ.get("ZENDESK_CLIENT_SECRET", "")

# -----------------------------------------------------------------------------------
#
#    1. Create an Facebook app on https://developers.facebook.com/apps/
#
#    2. Copy the Facebook Application ID
#
#    3. From Settings > Basic, show and copy the Facebook Application Secret
#
#    4. Generate a Random Secret to use as Facebook Webhook Secret as described
#       on https://developers.facebook.com/docs/messenger-platform/webhook#setup
#
# -----------------------------------------------------------------------------------
FACEBOOK_APPLICATION_ID = os.environ.get("FACEBOOK_APPLICATION_ID", "MISSING_FACEBOOK_APPLICATION_ID")
FACEBOOK_APPLICATION_SECRET = os.environ.get("FACEBOOK_APPLICATION_SECRET", "MISSING_FACEBOOK_APPLICATION_SECRET")
FACEBOOK_WEBHOOK_SECRET = os.environ.get("FACEBOOK_WEBHOOK_SECRET", "MISSING_FACEBOOK_WEBHOOK_SECRET")

WHATSAPP_ADMIN_SYSTEM_USER_ID = os.environ.get(
    "WHATSAPP_ADMIN_SYSTEM_USER_ID", "MISSING_WHATSAPP_ADMIN_SYSTEM_USER_ID"
)
WHATSAPP_ADMIN_SYSTEM_USER_TOKEN = os.environ.get(
    "WHATSAPP_ADMIN_SYSTEM_USER_TOKEN", "MISSING_WHATSAPP_ADMIN_SYSTEM_USER_TOKEN"
)
WHATSAPP_FACEBOOK_BUSINESS_ID = os.environ.get(
    "WHATSAPP_FACEBOOK_BUSINESS_ID", "MISSING_WHATSAPP_FACEBOOK_BUSINESS_ID"
)


# -----------------------------------------------------------------------------------
# IP Addresses
# These are the externally accessible IP addresses of the servers running RapidPro.
# Needed for channel types that authenticate by whitelisting public IPs.
#
# You need to change these to real addresses to work with these.
# -----------------------------------------------------------------------------------
IP_ADDRESSES = ("172.16.10.10", "162.16.10.20")

# -----------------------------------------------------------------------------------
# Data model limits
# -----------------------------------------------------------------------------------
MSG_FIELD_SIZE = 640  # used for broadcast text, message text, and message campaign events
FLOW_START_PARAMS_SIZE = 256  # used for params passed to flow start API endpoint
GLOBAL_VALUE_SIZE = 10_000  # max length of global values

ORG_LIMIT_DEFAULTS = {
    "channels": 10,
    "fields": 250,
    "globals": 250,
    "groups": 250,
    "labels": 250,
    "teams": 50,
    "topics": 250,
}

# -----------------------------------------------------------------------------------
# Data retention periods - tasks trim away data older than these settings
# -----------------------------------------------------------------------------------
RETENTION_PERIODS = {
    "channellog": timedelta(days=7),
    "eventfire": timedelta(days=90),  # matches default rp-archiver behavior
    "flowsession": timedelta(days=7),
    "httplog": timedelta(days=3),
    "syncevent": timedelta(days=7),
    "webhookevent": timedelta(hours=48),
}

# -----------------------------------------------------------------------------------
# Mailroom
# -----------------------------------------------------------------------------------
MAILROOM_URL = None
MAILROOM_AUTH_TOKEN = None

# -----------------------------------------------------------------------------------
# ElasticSearch
# -----------------------------------------------------------------------------------
ELASTICSEARCH_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
