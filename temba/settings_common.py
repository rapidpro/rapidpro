import os
import sys
from datetime import timedelta

import iptools
from celery.schedules import crontab

from django.utils.translation import gettext_lazy as _

INTERNAL_IPS = iptools.IpRangeList("127.0.0.1", "192.168.0.10", "192.168.0.0/24", "0.0.0.0")  # network block
HOSTNAME = "localhost"

# HTTP Headers using for outgoing requests to other services
OUTGOING_REQUEST_HEADERS = {"User-agent": "RapidPro"}

# Make this unique, and don't share it with anybody.
SECRET_KEY = "your own secret key"

DATA_UPLOAD_MAX_NUMBER_FIELDS = 2500  # needed for exports of big workspaces

# -----------------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------------
TESTING = sys.argv[1:2] == ["test"]

if TESTING:
    PASSWORD_HASHERS = ("django.contrib.auth.hashers.MD5PasswordHasher",)
    DEBUG = False

TEST_RUNNER = "temba.tests.runner.TembaTestRunner"
TEST_EXCLUDE = ("smartmin",)

# -----------------------------------------------------------------------------------
# Email
# -----------------------------------------------------------------------------------

SEND_EMAILS = TESTING  # enable sending emails in tests

EMAIL_HOST = "smtp.gmail.com"
EMAIL_HOST_USER = "server@temba.io"
DEFAULT_FROM_EMAIL = "server@temba.io"
EMAIL_HOST_PASSWORD = "mypassword"
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 10

# Used when sending email from within a flow and the user hasn't configured
# their own SMTP server.
FLOW_FROM_EMAIL = "no-reply@temba.io"

# -----------------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------------

STORAGES = {
    # default storage for things like exports, imports
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # wherever rp-archiver writes archive files (must be S3 compatible)
    "archives": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {"bucket_name": "temba-archives"},
    },
    # wherever courier and mailroom are writing logs
    "logs": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    # media file uploads that need to be publicly accessible
    "public": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # standard Django static files storage
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

STORAGE_URL = None  # may be an absolute URL to /media (like http://localhost:8000/media) or AWS S3
STORAGE_ROOT_DIR = "test_orgs" if TESTING else "orgs"

# settings used by django-storages
AWS_ACCESS_KEY_ID = "aws_access_key_id"
AWS_SECRET_ACCESS_KEY = "aws_secret_access_key"

# -----------------------------------------------------------------------------------
# Localization
# -----------------------------------------------------------------------------------

USE_TZ = True
TIME_ZONE = "GMT"
USER_TIME_ZONE = "Africa/Kigali"

LANGUAGE_CODE = "en-us"

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

USE_I18N = True
USE_L10N = True

# -----------------------------------------------------------------------------------
# Static Files
# -----------------------------------------------------------------------------------

# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "compressor.finders.CompressorFinder",
)


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

# -----------------------------------------------------------------------------------
# Templates
# -----------------------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            os.path.join(PROJECT_DIR, "../templates"),
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
            ],
            "loaders": [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
        },
    }
]

if TESTING:
    TEMPLATES[0]["OPTIONS"]["context_processors"] += ("temba.tests.add_testing_flag_to_context",)

FORM_RENDERER = "django.forms.renderers.TemplatesSetting"

# -----------------------------------------------------------------------------------
# Middleware
# -----------------------------------------------------------------------------------

MIDDLEWARE = (
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "temba.middleware.OrgMiddleware",
    "temba.middleware.LanguageMiddleware",
    "temba.middleware.TimezoneMiddleware",
    "temba.middleware.ToastMiddleware",
)

# -----------------------------------------------------------------------------------
# Apps
# -----------------------------------------------------------------------------------

ROOT_URLCONF = "temba.urls"

# other urls to add
APP_URLS = []

SITEMAP = ("public.public_index", "public.video_list", "api")

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
    "imagekit",
    "redis",
    "rest_framework",
    "rest_framework.authtoken",
    "compressor",
    "smartmin",
    "smartmin.csv_imports",
    "smartmin.users",
    "timezone_field",
    "temba.apks",
    "temba.archives",
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

# don't let smartmin auto create django messages for create and update submissions
SMARTMIN_DEFAULT_MESSAGES = False

# -----------------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------------

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
# Branding
# -----------------------------------------------------------------------------------

BRAND = {
    "name": "RapidPro",
    "description": _("Visually build nationally scalable mobile applications anywhere in the world."),
    "hosts": ["rapidpro.io"],
    "domain": "app.rapidpro.io",
    "emails": {"notifications": "support@rapidpro.io"},
    "logos": {
        "primary": "images/logo-dark.svg",
        "favico": "brands/rapidpro/rapidpro.ico",
        "avatar": "brands/rapidpro/rapidpro-avatar.webp",
    },
    "landing": {
        "hero": "brands/rapidpro/splash.jpg",
    },
    "features": ["signups"],
}

FEATURES = {"locations"}


# -----------------------------------------------------------------------------------
# Permissions
# -----------------------------------------------------------------------------------

PERMISSIONS = {
    "*": (
        "create",  # can create an object
        "read",  # can read an object, viewing it's details
        "update",  # can update an object
        "delete",  # can delete an object,
        "list",  # can view a list of the objects
    ),
    "api.apitoken": ("explorer",),
    "archives.archive": ("run", "message"),
    "campaigns.campaign": ("archived", "archive", "activate", "menu"),
    "channels.channel": ("chart", "claim", "configuration", "errors", "facebook_whitelist"),
    "channels.channellog": ("connection",),
    "classifiers.classifier": ("connect", "sync"),
    "contacts.contact": (
        "export",
        "history",
        "interrupt",
        "menu",
        "omnibox",
        "open_ticket",
        "start",
    ),
    "contacts.contactfield": ("update_priority",),
    "contacts.contactgroup": ("menu",),
    "contacts.contactimport": ("preview",),
    "flows.flow": ("assets", "copy", "editor", "export", "menu", "results", "start"),
    "flows.flowsession": ("json",),
    "globals.global": ("unused",),
    "locations.adminboundary": ("alias", "boundaries", "geometry"),
    "msgs.broadcast": ("scheduled", "scheduled_read", "scheduled_delete"),
    "msgs.msg": ("archive", "export", "label", "menu"),
    "orgs.export": ("download",),
    "orgs.org": (
        "country",
        "create",
        "dashboard",
        "delete_child",
        "download",
        "edit_sub_org",
        "edit",
        "export",
        "flow_smtp",
        "grant",
        "join_accept",
        "join",
        "languages",
        "manage_accounts_sub_org",
        "manage_accounts",
        "manage_integrations",
        "manage",
        "menu",
        "prometheus",
        "resthooks",
        "service",
        "signup",
        "spa",
        "sub_orgs",
        "trial",
        "twilio_account",
        "twilio_connect",
        "workspace",
    ),
    "orgs.user": ("token",),
    "request_logs.httplog": ("webhooks", "classifier"),
    "tickets.ticket": ("assign", "assignee", "menu", "note", "export_stats", "export"),
    "triggers.trigger": ("archived", "type", "menu"),
}


# assigns the permissions that each group should have
GROUP_PERMISSIONS = {
    "Alpha": (),
    "Beta": (),
    "Dashboard": ("orgs.org_dashboard",),
    "Surveyors": (),
    "Customer Support": (),
    "Granters": ("orgs.org_grant",),
    "Administrators": (
        "airtime.airtimetransfer_list",
        "airtime.airtimetransfer_read",
        "api.apitoken_explorer",
        "api.resthook_list",
        "api.resthooksubscriber_create",
        "api.resthooksubscriber_delete",
        "api.resthooksubscriber_list",
        "api.webhookevent_list",
        "archives.archive.*",
        "campaigns.campaign.*",
        "campaigns.campaignevent.*",
        "channels.channel_claim",
        "channels.channel_configuration",
        "channels.channel_create",
        "channels.channel_delete",
        "channels.channel_facebook_whitelist",
        "channels.channel_list",
        "channels.channel_read",
        "channels.channel_update",
        "channels.channelevent_list",
        "channels.channellog_list",
        "channels.channellog_read",
        "classifiers.classifier_connect",
        "classifiers.classifier_delete",
        "classifiers.classifier_list",
        "classifiers.classifier_read",
        "classifiers.classifier_sync",
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
        "flows.flowrun_list",
        "flows.flowstart.*",
        "globals.global.*",
        "ivr.call.*",
        "locations.adminboundary_alias",
        "locations.adminboundary_boundaries",
        "locations.adminboundary_geometry",
        "locations.adminboundary_list",
        "msgs.broadcast.*",
        "msgs.label.*",
        "msgs.media_create",
        "msgs.msg_archive",
        "msgs.msg_create",
        "msgs.msg_delete",
        "msgs.msg_export",
        "msgs.msg_label",
        "msgs.msg_list",
        "msgs.msg_menu",
        "msgs.msg_update",
        "msgs.optin.*",
        "notifications.incident.*",
        "notifications.notification.*",
        "orgs.export.*",
        "orgs.invitation.*",
        "orgs.org_country",
        "orgs.org_create",
        "orgs.org_dashboard",
        "orgs.org_delete_child",
        "orgs.org_download",
        "orgs.org_edit_sub_org",
        "orgs.org_edit",
        "orgs.org_export",
        "orgs.org_flow_smtp",
        "orgs.org_languages",
        "orgs.org_manage_accounts_sub_org",
        "orgs.org_manage_accounts",
        "orgs.org_manage_integrations",
        "orgs.org_menu",
        "orgs.org_prometheus",
        "orgs.org_read",
        "orgs.org_resthooks",
        "orgs.org_sub_orgs",
        "orgs.org_workspace",
        "orgs.orgimport.*",
        "orgs.user_list",
        "orgs.user_token",
        "request_logs.httplog_list",
        "request_logs.httplog_read",
        "request_logs.httplog_webhooks",
        "templates.template.*",
        "tickets.ticket.*",
        "tickets.topic.*",
        "triggers.trigger.*",
    ),
    "Editors": (
        "airtime.airtimetransfer_list",
        "airtime.airtimetransfer_read",
        "api.apitoken_explorer",
        "api.resthook_list",
        "api.resthooksubscriber_create",
        "api.resthooksubscriber_delete",
        "api.resthooksubscriber_list",
        "api.webhookevent_list",
        "archives.archive.*",
        "campaigns.campaign.*",
        "campaigns.campaignevent.*",
        "channels.channel_claim",
        "channels.channel_configuration",
        "channels.channel_create",
        "channels.channel_delete",
        "channels.channel_list",
        "channels.channel_read",
        "channels.channel_update",
        "channels.channelevent_list",
        "classifiers.classifier_list",
        "classifiers.classifier_read",
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
        "flows.flowrun_list",
        "flows.flowstart_create",
        "flows.flowstart_list",
        "globals.global.*",
        "ivr.call_list",
        "locations.adminboundary_alias",
        "locations.adminboundary_boundaries",
        "locations.adminboundary_geometry",
        "locations.adminboundary_list",
        "msgs.broadcast.*",
        "msgs.label.*",
        "msgs.media_create",
        "msgs.msg_archive",
        "msgs.msg_create",
        "msgs.msg_delete",
        "msgs.msg_export",
        "msgs.msg_label",
        "msgs.msg_list",
        "msgs.msg_menu",
        "msgs.msg_update",
        "msgs.optin_create",
        "msgs.optin_list",
        "notifications.notification_list",
        "orgs.export_download",
        "orgs.org_download",
        "orgs.org_export",
        "orgs.org_languages",
        "orgs.org_menu",
        "orgs.org_read",
        "orgs.org_resthooks",
        "orgs.org_workspace",
        "orgs.orgimport.*",
        "orgs.user_list",
        "orgs.user_token",
        "request_logs.httplog_webhooks",
        "templates.template_list",
        "templates.template_read",
        "tickets.ticket.*",
        "tickets.topic.*",
        "triggers.trigger.*",
    ),
    "Viewers": (
        "campaigns.campaign_archived",
        "campaigns.campaign_list",
        "campaigns.campaign_menu",
        "campaigns.campaign_read",
        "campaigns.campaignevent_list",
        "campaigns.campaignevent_read",
        "channels.channel_list",
        "channels.channel_read",
        "channels.channelevent_list",
        "classifiers.classifier_list",
        "classifiers.classifier_read",
        "contacts.contact_export",
        "contacts.contact_history",
        "contacts.contact_list",
        "contacts.contact_menu",
        "contacts.contact_read",
        "contacts.contactfield_list",
        "contacts.contactfield_read",
        "contacts.contactgroup_list",
        "contacts.contactgroup_menu",
        "contacts.contactgroup_read",
        "contacts.contactimport_read",
        "flows.flow_assets",
        "flows.flow_editor",
        "flows.flow_export",
        "flows.flow_list",
        "flows.flow_menu",
        "flows.flow_results",
        "flows.flowrun_list",
        "flows.flowstart_list",
        "globals.global_list",
        "globals.global_read",
        "ivr.call_list",
        "locations.adminboundary_alias",
        "locations.adminboundary_boundaries",
        "locations.adminboundary_geometry",
        "locations.adminboundary_list",
        "msgs.broadcast_list",
        "msgs.broadcast_scheduled",
        "msgs.broadcast_scheduled_read",
        "msgs.label_list",
        "msgs.label_read",
        "msgs.msg_export",
        "msgs.msg_list",
        "msgs.msg_menu",
        "msgs.optin_list",
        "notifications.notification_list",
        "orgs.export_download",
        "orgs.org_download",
        "orgs.org_export",
        "orgs.org_menu",
        "orgs.org_read",
        "orgs.org_workspace",
        "orgs.user_list",
        "templates.template_list",
        "templates.template_read",
        "tickets.ticket_export",
        "tickets.ticket_list",
        "tickets.ticket_menu",
        "tickets.topic_list",
        "triggers.trigger_list",
        "triggers.trigger_menu",
    ),
    "Agents": (
        "contacts.contact_history",
        "notifications.notification_list",
        "orgs.org_languages",
        "orgs.org_menu",
        "tickets.ticket_assign",
        "tickets.ticket_assignee",
        "tickets.ticket_list",
        "tickets.ticket_menu",
        "tickets.ticket_note",
        "tickets.ticket_update",
        "tickets.topic_list",
    ),
    "Prometheus": (),
}

# extra permissions that only apply to API requests (wildcard notation not supported here)
API_PERMISSIONS = {
    "Agents": (
        "contacts.contact_create",
        "contacts.contact_list",
        "contacts.contact_update",
        "contacts.contactfield_list",
        "contacts.contactgroup_list",
        "locations.adminboundary_list",
        "msgs.media_create",
        "msgs.msg_create",
        "orgs.org_read",
        "orgs.user_list",
    )
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

INVITATION_VALIDITY = timedelta(days=30)

_db_host = "localhost"
_redis_host = "localhost"

if os.getenv("REMOTE_CONTAINERS") == "true":
    _db_host = "postgres"
    _redis_host = "redis"

# -----------------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------------
_default_database_config = {
    "ENGINE": "django.contrib.gis.db.backends.postgis",
    "NAME": "temba",
    "USER": "temba",
    "PASSWORD": "temba",
    "HOST": _db_host,
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

# -----------------------------------------------------------------------------------
# Cache
# -----------------------------------------------------------------------------------
_redis_url = f"redis://{_redis_host}:6379/{10 if TESTING else 15}"

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": _redis_url,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_CACHE_ALIAS = "default"

# -----------------------------------------------------------------------------------
# Celery
# -----------------------------------------------------------------------------------

CELERY_BROKER_URL = _redis_url
CELERY_RESULT_BACKEND = None
CELERY_TASK_TRACK_STARTED = True

# by default, celery doesn't have any timeout on our redis connections, this fixes that
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_timeout": 5}

CELERY_BEAT_SCHEDULE = {
    "check-android-channels": {"task": "check_android_channels", "schedule": timedelta(seconds=300)},
    "delete-released-orgs": {"task": "delete_released_orgs", "schedule": crontab(hour=4, minute=0)},
    "expire-invitations": {"task": "expire_invitations", "schedule": crontab(hour=0, minute=10)},
    "fail-old-android-messages": {"task": "fail_old_android_messages", "schedule": crontab(hour=0, minute=0)},
    "interrupt-flow-sessions": {"task": "interrupt_flow_sessions", "schedule": crontab(hour=23, minute=30)},
    "refresh-whatsapp-tokens": {"task": "refresh_whatsapp_tokens", "schedule": crontab(hour=6, minute=0)},
    "refresh-templates": {"task": "refresh_templates", "schedule": timedelta(seconds=900)},
    "send-notification-emails": {"task": "send_notification_emails", "schedule": timedelta(seconds=60)},
    "squash-channel-counts": {"task": "squash_channel_counts", "schedule": timedelta(seconds=60)},
    "squash-group-counts": {"task": "squash_group_counts", "schedule": timedelta(seconds=60)},
    "squash-flow-counts": {"task": "squash_flow_counts", "schedule": timedelta(seconds=60)},
    "squash-msg-counts": {"task": "squash_msg_counts", "schedule": timedelta(seconds=60)},
    "squash-notification-counts": {"task": "squash_notification_counts", "schedule": timedelta(seconds=60)},
    "squash-ticket-counts": {"task": "squash_ticket_counts", "schedule": timedelta(seconds=60)},
    "sync-classifier-intents": {"task": "sync_classifier_intents", "schedule": timedelta(seconds=300)},
    "track-org-channel-counts": {"task": "track_org_channel_counts", "schedule": crontab(hour=4, minute=0)},
    "trim-channel-events": {"task": "trim_channel_events", "schedule": crontab(hour=3, minute=0)},
    "trim-channel-logs": {"task": "trim_channel_logs", "schedule": crontab(hour=3, minute=0)},
    "trim-channel-sync-events": {"task": "trim_channel_sync_events", "schedule": crontab(hour=3, minute=0)},
    "trim-event-fires": {"task": "trim_event_fires", "schedule": timedelta(seconds=900)},
    "trim-exports": {"task": "trim_exports", "schedule": crontab(hour=2, minute=0)},
    "trim-flow-revisions": {"task": "trim_flow_revisions", "schedule": crontab(hour=0, minute=0)},
    "trim-flow-sessions": {"task": "trim_flow_sessions", "schedule": crontab(hour=0, minute=0)},
    "trim-http-logs": {"task": "trim_http_logs", "schedule": crontab(hour=2, minute=0)},
    "trim-notifications": {"task": "trim_notifications", "schedule": crontab(hour=2, minute=0)},
    "trim-webhook-events": {"task": "trim_webhook_events", "schedule": crontab(hour=3, minute=0)},
}

# -----------------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_THROTTLE_RATES": {
        "v2": "2500/hour",
        "v2.contacts": "2500/hour",
        "v2.messages": "2500/hour",
        "v2.broadcasts": "36000/hour",
        "v2.runs": "2500/hour",
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 250,
    "EXCEPTION_HANDLER": "temba.api.support.temba_exception_handler",
}
REST_HANDLE_EXCEPTIONS = not TESTING

# -----------------------------------------------------------------------------------
# Compression
# -----------------------------------------------------------------------------------

if TESTING:
    # if only testing, disable less compilation
    COMPRESS_PRECOMPILERS = ()
else:
    COMPRESS_PRECOMPILERS = (
        ("text/less", 'lessc --include-path="%s" {infile} {outfile}' % os.path.join(PROJECT_DIR, "../static", "less")),
    )

COMPRESS_FILTERS = {
    "css": ["compressor.filters.css_default.CssAbsoluteFilter"],
    "js": [],
}

COMPRESS_ENABLED = False
COMPRESS_OFFLINE = False

# -----------------------------------------------------------------------------------
# Pluggable Types
# -----------------------------------------------------------------------------------

INTEGRATION_TYPES = [
    "temba.orgs.integrations.dtone.DTOneType",
]

CLASSIFIER_TYPES = [
    "temba.classifiers.types.wit.WitType",
    "temba.classifiers.types.luis.LuisType",
    "temba.classifiers.types.bothub.BothubType",
]

CHANNEL_TYPES = [
    "temba.channels.types.africastalking.AfricasTalkingType",
    "temba.channels.types.arabiacell.ArabiaCellType",
    "temba.channels.types.bandwidth.BandwidthType",
    "temba.channels.types.bongolive.BongoLiveType",
    "temba.channels.types.burstsms.BurstSMSType",
    "temba.channels.types.clickatell.ClickatellType",
    "temba.channels.types.clickmobile.ClickMobileType",
    "temba.channels.types.clicksend.ClickSendType",
    "temba.channels.types.dartmedia.DartMediaType",
    "temba.channels.types.dialog360_legacy.Dialog360LegacyType",
    "temba.channels.types.dialog360.Dialog360Type",
    "temba.channels.types.discord.DiscordType",
    "temba.channels.types.dmark.DMarkType",
    "temba.channels.types.external.ExternalType",
    "temba.channels.types.facebook_legacy.FacebookLegacyType",
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
    "temba.channels.types.justcall.JustCallType",
    "temba.channels.types.kaleyra.KaleyraType",
    "temba.channels.types.kannel.KannelType",
    "temba.channels.types.line.LineType",
    "temba.channels.types.m3tech.M3TechType",
    "temba.channels.types.macrokiosk.MacrokioskType",
    "temba.channels.types.mblox.MbloxType",
    "temba.channels.types.mailgun.MailgunType",
    "temba.channels.types.messagebird.MessageBirdType",
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
    "temba.channels.types.twitter.TwitterType",
    "temba.channels.types.verboice.VerboiceType",
    "temba.channels.types.viber_public.ViberPublicType",
    "temba.channels.types.vk.VKType",
    "temba.channels.types.vonage.VonageType",
    "temba.channels.types.wavy.WavyType",
    "temba.channels.types.wechat.WeChatType",
    "temba.channels.types.whatsapp.WhatsAppType",
    "temba.channels.types.whatsapp_legacy.WhatsAppLegacyType",
    "temba.channels.types.yo.YoType",
    "temba.channels.types.zenvia_sms.ZenviaSMSType",
    "temba.channels.types.zenvia_whatsapp.ZenviaWhatsAppType",
    "temba.channels.types.android.AndroidType",
]

ANALYTICS_TYPES = [
    "temba.utils.analytics.ConsoleBackend",
]

# set of ISO-639-3 codes of languages to allow in addition to all ISO-639-1 languages
NON_ISO6391_LANGUAGES = {"mul", "und"}

# -----------------------------------------------------------------------------------
# Mailroom
# -----------------------------------------------------------------------------------

MAILROOM_URL = None
MAILROOM_AUTH_TOKEN = None

# -----------------------------------------------------------------------------------
# Data Model
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

RETENTION_PERIODS = {
    "channelevent": timedelta(days=90),
    "channellog": timedelta(days=14),
    "export": timedelta(days=90),
    "eventfire": timedelta(days=90),
    "flowsession": timedelta(days=7),
    "httplog": timedelta(days=3),
    "notification": timedelta(days=30),
    "syncevent": timedelta(days=7),
    "webhookevent": timedelta(hours=48),
}

# -----------------------------------------------------------------------------------
# 3rd Party Integrations
# -----------------------------------------------------------------------------------

TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY", "MISSING_TWITTER_API_KEY")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET", "MISSING_TWITTER_API_SECRET")

MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY", "")

ZENDESK_CLIENT_ID = os.environ.get("ZENDESK_CLIENT_ID", "")
ZENDESK_CLIENT_SECRET = os.environ.get("ZENDESK_CLIENT_SECRET", "")


#    1. Create an Facebook app on https://developers.facebook.com/apps/
#
#    2. Copy the Facebook Application ID
#
#    3. From Settings > Basic, show and copy the Facebook Application Secret
#
#    4. Generate a Random Secret to use as Facebook Webhook Secret as described
#       on https://developers.facebook.com/docs/messenger-platform/webhook#setup
#
FACEBOOK_APPLICATION_ID = os.environ.get("FACEBOOK_APPLICATION_ID", "MISSING_FACEBOOK_APPLICATION_ID")
FACEBOOK_APPLICATION_SECRET = os.environ.get("FACEBOOK_APPLICATION_SECRET", "MISSING_FACEBOOK_APPLICATION_SECRET")
FACEBOOK_WEBHOOK_SECRET = os.environ.get("FACEBOOK_WEBHOOK_SECRET", "MISSING_FACEBOOK_WEBHOOK_SECRET")

# Facebook login for business config IDs
FACEBOOK_LOGIN_WHATSAPP_CONFIG_ID = os.environ.get("FACEBOOK_LOGIN_WHATSAPP_CONFIG_ID", "")
FACEBOOK_LOGIN_INSTAGRAM_CONFIG_ID = os.environ.get("FACEBOOK_LOGIN_INSTAGRAM_CONFIG_ID", "")
FACEBOOK_LOGIN_MESSENGER_CONFIG_ID = os.environ.get("FACEBOOK_LOGIN_MESSENGER_CONFIG_ID", "")

WHATSAPP_ADMIN_SYSTEM_USER_ID = os.environ.get("WHATSAPP_ADMIN_SYSTEM_USER_ID", "MISSING_WHATSAPP_ADMIN_SYSTEM_USER_ID")
WHATSAPP_ADMIN_SYSTEM_USER_TOKEN = os.environ.get(
    "WHATSAPP_ADMIN_SYSTEM_USER_TOKEN", "MISSING_WHATSAPP_ADMIN_SYSTEM_USER_TOKEN"
)
WHATSAPP_FACEBOOK_BUSINESS_ID = os.environ.get("WHATSAPP_FACEBOOK_BUSINESS_ID", "MISSING_WHATSAPP_FACEBOOK_BUSINESS_ID")

# IP Addresses
# These are the externally accessible IP addresses of the servers running RapidPro.
# Needed for channel types that authenticate by whitelisting public IPs.
#
# You need to change these to real addresses to work with these.
IP_ADDRESSES = ("172.16.10.10", "162.16.10.20")

# Android clients FCM config
ANDROID_FCM_PROJECT_ID = os.environ.get("ANDROID_FCM_PROJECT_ID", "")
ANDROID_CREDENTIALS_FILE = os.environ.get("ANDROID_CREDENTIALS_FILE", "")
