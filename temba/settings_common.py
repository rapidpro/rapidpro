import os
import socket
import sys
from datetime import timedelta

import iptools

from django.utils.translation import ugettext_lazy as _

from celery.schedules import crontab

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

# hardcode the postgis version so we can do reset db's from a blank database
POSTGIS_VERSION = (2, 1)

# -----------------------------------------------------------------------------------
# set the mail settings, override these in your settings.py
# if your site was at http://temba.io, it might look like this:
# -----------------------------------------------------------------------------------
EMAIL_HOST = "smtp.gmail.com"
EMAIL_HOST_USER = "server@temba.io"
DEFAULT_FROM_EMAIL = "server@temba.io"
EMAIL_HOST_PASSWORD = "mypassword"
EMAIL_USE_TLS = True

# Used when sending email from within a flow and the user hasn't configured
# their own SMTP server.
FLOW_FROM_EMAIL = "no-reply@temba.io"

# HTTP Headers using for outgoing requests to other services
OUTGOING_REQUEST_HEADERS = {"User-agent": "RapidPro"}

STORAGE_URL = None  # may be local /media or AWS S3
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

MODELTRANSLATION_TRANSLATION_REGISTRY = "translation"

# -----------------------------------------------------------------------------------
# Default language used for this installation
# -----------------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"

# -----------------------------------------------------------------------------------
# Available languages for translation
# -----------------------------------------------------------------------------------
LANGUAGES = (("en-us", _("English")), ("pt-br", _("Portuguese")), ("fr", _("French")), ("es", _("Spanish")))

DEFAULT_LANGUAGE = "en-us"
DEFAULT_SMS_LANGUAGE = "en-us"

SITE_ID = 1

# If you set this to False, Django will make some optimizations so as not
# to load the internationalization machinery.
USE_I18N = True

# If you set this to False, Django will not format dates, numbers and
# calendars according to the current locale
USE_L10N = True

# URL prefix for admin static files -- CSS, JavaScript and images.
# Make sure to use a trailing slash.
# Examples: "http://foo.com/static/admin/", "/static/admin/".
ADMIN_MEDIA_PREFIX = "/static/admin/"

# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "compressor.finders.CompressorFinder",
)

# Make this unique, and don't share it with anybody.
SECRET_KEY = "your own secret key"

EMAIL_CONTEXT_PROCESSORS = ("temba.utils.email.link_components",)


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
    os.path.join(PROJECT_DIR, "../node_modules/react/umd"),
    os.path.join(PROJECT_DIR, "../node_modules/react-dom/umd"),
)
STATIC_ROOT = os.path.join(PROJECT_DIR, "../sitestatic")
STATIC_URL = "/sitestatic/"
COMPRESS_ROOT = os.path.join(PROJECT_DIR, "../sitestatic")
MEDIA_ROOT = os.path.join(PROJECT_DIR, "../media")
MEDIA_URL = "/media/"


# -----------------------------------------------------------------------------------
# Templates Configuration
# -----------------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(PROJECT_DIR, "../templates")],
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
                "temba.orgs.context_processors.user_group_perms_processor",
                "temba.channels.views.channel_status_processor",
                "temba.msgs.views.send_message_auto_complete_processor",
                "temba.orgs.context_processors.settings_includer",
                "temba.orgs.context_processors.user_orgs_for_brand",
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
    "temba.middleware.ConsentMiddleware",
    "temba.middleware.BrandingMiddleware",
    "temba.middleware.OrgTimezoneMiddleware",
    "temba.middleware.ActivateLanguageMiddleware",
    "temba.middleware.OrgHeaderMiddleware",
)

# security middleware configuration
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True

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
    "temba.policies",
    "temba.schedules",
    "temba.templates",
    "temba.orgs",
    "temba.contacts",
    "temba.channels",
    "temba.msgs",
    "temba.flows",
    "temba.triggers",
    "temba.utils",
    "temba.campaigns",
    "temba.ivr",
    "temba.locations",
    "temba.values",
    "temba.airtime",
    "temba.sql",
)

# the last installed app that uses smartmin permissions
PERMISSIONS_APP = "temba.airtime"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "root": {"level": "WARNING", "handlers": ["console"]},
    "formatters": {"verbose": {"format": "%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s"}},
    "handlers": {
        "console": {"level": "DEBUG", "class": "logging.StreamHandler", "formatter": "verbose"},
        "null": {"class": "logging.NullHandler"},
    },
    "loggers": {
        "pycountry": {"level": "ERROR", "handlers": ["console"], "propagate": False},
        "django.security.DisallowedHost": {"handlers": ["null"], "propagate": False},
        "django.db.backends": {"level": "ERROR", "handlers": ["console"], "propagate": False},
    },
}

# -----------------------------------------------------------------------------------
# Branding Configuration
# -----------------------------------------------------------------------------------
BRANDING = {
    "rapidpro.io": {
        "slug": "rapidpro",
        "name": "RapidPro",
        "org": "UNICEF",
        "colors": dict(primary="#0c6596"),
        "styles": ["brands/rapidpro/font/style.css"],
        "welcome_topup": 1000,
        "email": "join@rapidpro.io",
        "support_email": "support@rapidpro.io",
        "link": "https://app.rapidpro.io",
        "api_link": "https://api.rapidpro.io",
        "docs_link": "http://docs.rapidpro.io",
        "domain": "app.rapidpro.io",
        "favico": "brands/rapidpro/rapidpro.ico",
        "splash": "brands/rapidpro/splash.jpg",
        "logo": "brands/rapidpro/logo.png",
        "allow_signups": True,
        "flow_types": ["M", "V", "S"],  # see Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_SURVEY
        "tiers": dict(import_flows=0, multi_user=0, multi_org=0),
        "bundles": [],
        "welcome_packs": [dict(size=5000, name="Demo Account"), dict(size=100000, name="UNICEF Account")],
        "description": _("Visually build nationally scalable mobile applications from anywhere in the world."),
        "credits": _("Copyright &copy; 2012-2017 UNICEF, Nyaruka. All Rights Reserved."),
    }
}
DEFAULT_BRAND = "rapidpro.io"

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
    "campaigns.campaign": ("api", "archived", "archive", "activate"),
    "campaigns.campaignevent": ("api",),
    "classifiers.classifier": ("connect", "api", "sync"),
    "classifiers.intent": ("api",),
    "contacts.contact": (
        "api",
        "block",
        "blocked",
        "break_anon",
        "customize",
        "export",
        "stopped",
        "filter",
        "history",
        "import",
        "omnibox",
        "search",
        "unblock",
        "unstop",
        "update_fields",
        "update_fields_input",
    ),
    "contacts.contactfield": ("api", "json", "update_priority", "featured", "filter_by_type", "detail"),
    "contacts.contactgroup": ("api",),
    "ivr.ivrcall": ("start",),
    "archives.archive": ("api", "run", "message"),
    "globals.global": ("api", "unused", "detail"),
    "locations.adminboundary": ("alias", "api", "boundaries", "geometry"),
    "orgs.org": (
        "accounts",
        "smtp_server",
        "api",
        "country",
        "chatbase",
        "clear_cache",
        "create_login",
        "create_sub_org",
        "dashboard",
        "download",
        "dtone_account",
        "edit",
        "edit_sub_org",
        "export",
        "grant",
        "home",
        "import",
        "join",
        "languages",
        "manage",
        "manage_accounts",
        "manage_accounts_sub_org",
        "nexmo_account",
        "nexmo_connect",
        "plivo_connect",
        "profile",
        "resthooks",
        "service",
        "signup",
        "sub_orgs",
        "surveyor",
        "transfer_credits",
        "trial",
        "twilio_account",
        "twilio_connect",
        "token",
    ),
    "orgs.usersettings": ("phone",),
    "channels.channel": (
        "api",
        "bulk_sender_options",
        "claim",
        "configuration",
        "create_bulk_sender",
        "create_caller",
        "errors",
        "facebook_whitelist",
        "search_nexmo",
        "search_numbers",
    ),
    "channels.channellog": ("connection",),
    "channels.channelevent": ("api", "calls"),
    "flows.flowstart": ("api",),
    "flows.flow": (
        "activity",
        "activity_chart",
        "activity_list",
        "api",
        "archived",
        "assets",
        "broadcast",
        "campaign",
        "category_counts",
        "completion",
        "copy",
        "editor",
        "editor_next",
        "export",
        "export_results",
        "filter",
        "json",
        "recent_messages",
        "results",
        "revisions",
        "run_table",
        "simulate",
        "upload_action_recording",
        "upload_media_action",
    ),
    "flows.flowsession": ("json",),
    "msgs.msg": (
        "api",
        "archive",
        "archived",
        "export",
        "failed",
        "filter",
        "flow",
        "inbox",
        "label",
        "outbox",
        "sent",
        "update",
    ),
    "msgs.broadcast": ("api", "detail", "schedule", "schedule_list", "schedule_read", "send"),
    "msgs.label": ("api", "create", "create_folder"),
    "orgs.topup": ("manage",),
    "policies.policy": ("admin", "history", "give_consent"),
    "templates.template": ("api",),
    "triggers.trigger": (
        "archived",
        "catchall",
        "follow",
        "inbound_call",
        "keyword",
        "missed_call",
        "new_conversation",
        "referral",
        "register",
        "schedule",
    ),
}


# assigns the permissions that each group should have
GROUP_PERMISSIONS = {
    "Service Users": ("flows.flow_assets", "msgs.msg_create"),  # internal Temba services have limited permissions
    "Alpha": (),
    "Beta": (),
    "Dashboard": ("orgs.org_dashboard",),
    "Surveyors": (
        "contacts.contact_api",
        "contacts.contactgroup_api",
        "contacts.contactfield_api",
        "flows.flow_api",
        "locations.adminboundary_api",
        "orgs.org_api",
        "orgs.org_surveyor",
        "msgs.msg_api",
    ),
    "Granters": ("orgs.org_grant",),
    "Customer Support": (
        "auth.user_list",
        "auth.user_update",
        "apks.apk_create",
        "apks.apk_list",
        "apks.apk_update",
        "campaigns.campaign_read",
        "channels.channel_configuration",
        "channels.channel_read",
        "contacts.contact_break_anon",
        "contacts.contact_read",
        "flows.flow_editor",
        "flows.flow_json",
        "flows.flow_revisions",
        "flows.flowrun_delete",
        "flows.flow_editor_next",
        "flows.flowsession_json",
        "orgs.org_dashboard",
        "orgs.org_delete",
        "orgs.org_grant",
        "orgs.org_manage",
        "orgs.org_update",
        "orgs.org_service",
        "orgs.topup_create",
        "orgs.topup_manage",
        "orgs.topup_update",
        "policies.policy_create",
        "policies.policy_update",
        "policies.policy_admin",
        "policies.policy_history",
    ),
    "Administrators": (
        "airtime.airtimetransfer_list",
        "airtime.airtimetransfer_read",
        "api.apitoken_refresh",
        "api.resthook_api",
        "api.resthook_list",
        "api.resthooksubscriber_api",
        "api.webhookevent_api",
        "api.webhookresult_list",
        "api.webhookresult_read",
        "archives.archive.*",
        "campaigns.campaign.*",
        "campaigns.campaignevent.*",
        "classifiers.classifier_api",
        "classifiers.classifier_connect",
        "classifiers.classifier_read",
        "classifiers.classifier_delete",
        "classifiers.classifier_list",
        "classifiers.classifier_sync",
        "classifiers.intent_api",
        "contacts.contact_api",
        "contacts.contact_block",
        "contacts.contact_blocked",
        "contacts.contact_create",
        "contacts.contact_customize",
        "contacts.contact_delete",
        "contacts.contact_export",
        "contacts.contact_filter",
        "contacts.contact_history",
        "contacts.contact_import",
        "contacts.contact_list",
        "contacts.contact_omnibox",
        "contacts.contact_read",
        "contacts.contact_search",
        "contacts.contact_stopped",
        "contacts.contact_unblock",
        "contacts.contact_unstop",
        "contacts.contact_update",
        "contacts.contact_update_fields",
        "contacts.contact_update_fields_input",
        "contacts.contactfield.*",
        "contacts.contactgroup.*",
        "csv_imports.importtask.*",
        "globals.global.*",
        "ivr.ivrcall.*",
        "locations.adminboundary_alias",
        "locations.adminboundary_api",
        "locations.adminboundary_boundaries",
        "locations.adminboundary_geometry",
        "orgs.org_accounts",
        "orgs.org_smtp_server",
        "orgs.org_api",
        "orgs.org_country",
        "orgs.org_chatbase",
        "orgs.org_create_sub_org",
        "orgs.org_dashboard",
        "orgs.org_download",
        "orgs.org_dtone_account",
        "orgs.org_edit",
        "orgs.org_edit_sub_org",
        "orgs.org_export",
        "orgs.org_home",
        "orgs.org_import",
        "orgs.org_languages",
        "orgs.org_manage_accounts",
        "orgs.org_manage_accounts_sub_org",
        "orgs.org_nexmo_account",
        "orgs.org_nexmo_connect",
        "orgs.org_plivo_connect",
        "orgs.org_profile",
        "orgs.org_resthooks",
        "orgs.org_sub_orgs",
        "orgs.org_transfer_credits",
        "orgs.org_twilio_account",
        "orgs.org_twilio_connect",
        "orgs.org_token",
        "orgs.topup_list",
        "orgs.topup_read",
        "orgs.usersettings_phone",
        "orgs.usersettings_update",
        "channels.channel_api",
        "channels.channel_bulk_sender_options",
        "channels.channel_claim",
        "channels.channel_configuration",
        "channels.channel_create",
        "channels.channel_create_bulk_sender",
        "channels.channel_create_caller",
        "channels.channel_facebook_whitelist",
        "channels.channel_delete",
        "channels.channel_list",
        "channels.channel_read",
        "channels.channel_search_nexmo",
        "channels.channel_search_numbers",
        "channels.channel_update",
        "channels.channelevent.*",
        "channels.channellog_list",
        "channels.channellog_read",
        "channels.channellog_connection",
        "flows.flow.*",
        "flows.flowstart_api",
        "flows.flowlabel.*",
        "flows.ruleset.*",
        "flows.flowrun_delete",
        "schedules.schedule.*",
        "msgs.broadcast.*",
        "msgs.broadcastschedule.*",
        "msgs.label.*",
        "msgs.msg_api",
        "msgs.msg_archive",
        "msgs.msg_archived",
        "msgs.msg_delete",
        "msgs.msg_export",
        "msgs.msg_failed",
        "msgs.msg_filter",
        "msgs.msg_flow",
        "msgs.msg_inbox",
        "msgs.msg_label",
        "msgs.msg_outbox",
        "msgs.msg_sent",
        "msgs.msg_update",
        "policies.policy_read",
        "policies.policy_list",
        "policies.policy_give_consent",
        "request_logs.httplog_list",
        "request_logs.httplog_read",
        "templates.template_api",
        "triggers.trigger.*",
    ),
    "Editors": (
        "api.apitoken_refresh",
        "api.resthook_api",
        "api.resthook_list",
        "api.resthooksubscriber_api",
        "api.webhookevent_api",
        "api.webhookevent_list",
        "api.webhookevent_read",
        "archives.archive.*",
        "airtime.airtimetransfer_list",
        "airtime.airtimetransfer_read",
        "campaigns.campaign.*",
        "campaigns.campaignevent.*",
        "classifiers.classifier_api",
        "classifiers.classifier_read",
        "classifiers.classifier_list",
        "classifiers.intent_api",
        "contacts.contact_api",
        "contacts.contact_block",
        "contacts.contact_blocked",
        "contacts.contact_create",
        "contacts.contact_customize",
        "contacts.contact_delete",
        "contacts.contact_export",
        "contacts.contact_filter",
        "contacts.contact_history",
        "contacts.contact_import",
        "contacts.contact_list",
        "contacts.contact_omnibox",
        "contacts.contact_read",
        "contacts.contact_search",
        "contacts.contact_stopped",
        "contacts.contact_unblock",
        "contacts.contact_unstop",
        "contacts.contact_update",
        "contacts.contact_update_fields",
        "contacts.contact_update_fields_input",
        "contacts.contactfield.*",
        "contacts.contactgroup.*",
        "csv_imports.importtask.*",
        "ivr.ivrcall.*",
        "globals.global_api",
        "locations.adminboundary_alias",
        "locations.adminboundary_api",
        "locations.adminboundary_boundaries",
        "locations.adminboundary_geometry",
        "orgs.org_api",
        "orgs.org_download",
        "orgs.org_export",
        "orgs.org_home",
        "orgs.org_import",
        "orgs.org_profile",
        "orgs.org_resthooks",
        "orgs.topup_list",
        "orgs.topup_read",
        "orgs.usersettings_phone",
        "orgs.usersettings_update",
        "channels.channel_api",
        "channels.channel_bulk_sender_options",
        "channels.channel_claim",
        "channels.channel_configuration",
        "channels.channel_create",
        "channels.channel_create_bulk_sender",
        "channels.channel_create_caller",
        "channels.channel_delete",
        "channels.channel_list",
        "channels.channel_read",
        "channels.channel_search_numbers",
        "channels.channel_update",
        "channels.channelevent.*",
        "flows.flow.*",
        "flows.flowstart_api",
        "flows.flowlabel.*",
        "flows.ruleset.*",
        "schedules.schedule.*",
        "msgs.broadcast.*",
        "msgs.broadcastschedule.*",
        "msgs.label.*",
        "msgs.msg_api",
        "msgs.msg_archive",
        "msgs.msg_archived",
        "msgs.msg_delete",
        "msgs.msg_export",
        "msgs.msg_failed",
        "msgs.msg_filter",
        "msgs.msg_flow",
        "msgs.msg_inbox",
        "msgs.msg_label",
        "msgs.msg_outbox",
        "msgs.msg_sent",
        "msgs.msg_update",
        "policies.policy_read",
        "policies.policy_list",
        "policies.policy_give_consent",
        "templates.template_api",
        "triggers.trigger.*",
    ),
    "Viewers": (
        "api.resthook_list",
        "campaigns.campaign_archived",
        "campaigns.campaign_list",
        "campaigns.campaign_read",
        "campaigns.campaignevent_read",
        "classifiers.classifier_api",
        "classifiers.classifier_read",
        "classifiers.classifier_list",
        "classifiers.intent_api",
        "contacts.contact_blocked",
        "contacts.contact_export",
        "contacts.contact_filter",
        "contacts.contact_history",
        "contacts.contact_list",
        "contacts.contact_read",
        "contacts.contact_stopped",
        "contacts.contactfield_api",
        "contacts.contactgroup_api",
        "globals.global_api",
        "locations.adminboundary_boundaries",
        "locations.adminboundary_geometry",
        "locations.adminboundary_alias",
        "orgs.org_download",
        "orgs.org_export",
        "orgs.org_home",
        "orgs.org_profile",
        "orgs.topup_list",
        "orgs.topup_read",
        "channels.channel_list",
        "channels.channel_read",
        "channels.channelevent_calls",
        "flows.flow_activity",
        "flows.flow_activity_chart",
        "flows.flow_archived",
        "flows.flow_assets",
        "flows.flow_campaign",
        "flows.flow_completion",
        "flows.flow_category_counts",
        "flows.flow_export",
        "flows.flow_export_results",
        "flows.flow_filter",
        "flows.flow_list",
        "flows.flow_editor",
        "flows.flow_editor_next",
        "flows.flow_json",
        "flows.flow_recent_messages",
        "flows.flow_results",
        "flows.flow_revisions",
        "flows.flow_run_table",
        "flows.flow_simulate",
        "msgs.broadcast_schedule_list",
        "msgs.broadcast_schedule_read",
        "msgs.label_api",
        "msgs.msg_archived",
        "msgs.msg_export",
        "msgs.msg_failed",
        "msgs.msg_filter",
        "msgs.msg_flow",
        "msgs.msg_inbox",
        "msgs.msg_outbox",
        "msgs.msg_sent",
        "policies.policy_read",
        "policies.policy_list",
        "policies.policy_give_consent",
        "triggers.trigger_archived",
        "triggers.trigger_list",
    ),
}

# -----------------------------------------------------------------------------------
# Login / Logout
# -----------------------------------------------------------------------------------
LOGIN_URL = "/users/login/"
LOGOUT_URL = "/users/logout/"
LOGIN_REDIRECT_URL = "/org/choose/"
LOGOUT_REDIRECT_URL = "/"

AUTHENTICATION_BACKENDS = ("smartmin.backends.CaseInsensitiveBackend",)

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
}

_direct_database_config = _default_database_config.copy()
_default_database_config["DISABLE_SERVER_SIDE_CURSORS"] = True

DATABASES = {"default": _default_database_config, "direct": _direct_database_config}

# If we are testing, set both our connections as the same, Django seems to get
# confused on Python 3.6 with transactional tests otherwise
if TESTING:
    DATABASES["default"] = _direct_database_config

INTERNAL_IPS = iptools.IpRangeList("127.0.0.1", "192.168.0.10", "192.168.0.0/24", "0.0.0.0")  # network block

# -----------------------------------------------------------------------------------
# Crontab Settings ..
# -----------------------------------------------------------------------------------
CELERYBEAT_SCHEDULE = {
    "check-channels": {"task": "check_channels_task", "schedule": timedelta(seconds=300)},
    "sync-old-seen-channels": {"task": "sync_old_seen_channels_task", "schedule": timedelta(seconds=600)},
    "sync-classifier-intents": {"task": "sync_classifier_intents", "schedule": timedelta(seconds=300)},
    "check-credits": {"task": "check_credits_task", "schedule": timedelta(seconds=900)},
    "check-topup-expiration": {"task": "check_topup_expiration_task", "schedule": crontab(hour=2, minute=0)},
    "check-elasticsearch-lag": {"task": "check_elasticsearch_lag", "schedule": timedelta(seconds=300)},
    "retry-errored-messages": {"task": "retry_errored_messages", "schedule": timedelta(seconds=60)},
    "fail-old-messages": {"task": "fail_old_messages", "schedule": crontab(hour=0, minute=0)},
    "trim-sync-events": {"task": "trim_sync_events_task", "schedule": crontab(hour=3, minute=0)},
    "trim-channel-log": {"task": "trim_channel_log_task", "schedule": crontab(hour=3, minute=0)},
    "trim-http-logs": {"task": "trim_http_logs_task", "schedule": crontab(hour=3, minute=0)},
    "trim-webhook-event": {"task": "trim_webhook_event_task", "schedule": crontab(hour=3, minute=0)},
    "trim-event-fires": {"task": "trim_event_fires_task", "schedule": timedelta(seconds=900)},
    "trim-flow-revisions": {"task": "trim_flow_revisions", "schedule": crontab(hour=0, minute=0)},
    "trim-flow-sessions": {"task": "trim_flow_sessions", "schedule": crontab(hour=0, minute=0)},
    "squash-flowruncounts": {"task": "squash_flowruncounts", "schedule": timedelta(seconds=60)},
    "squash-flowpathcounts": {"task": "squash_flowpathcounts", "schedule": timedelta(seconds=60)},
    "squash-channelcounts": {"task": "squash_channelcounts", "schedule": timedelta(seconds=60)},
    "squash-msgcounts": {"task": "squash_msgcounts", "schedule": timedelta(seconds=60)},
    "squash-topupcredits": {"task": "squash_topupcredits", "schedule": timedelta(seconds=60)},
    "squash-contactgroupcounts": {"task": "squash_contactgroupcounts", "schedule": timedelta(seconds=60)},
    "resolve-twitter-ids-task": {"task": "resolve_twitter_ids_task", "schedule": timedelta(seconds=900)},
    "refresh-jiochat-access-tokens": {"task": "refresh_jiochat_access_tokens", "schedule": timedelta(seconds=3600)},
    "refresh-wechat-access-tokens": {"task": "refresh_wechat_access_tokens", "schedule": timedelta(seconds=3600)},
    "refresh-whatsapp-tokens": {"task": "refresh_whatsapp_tokens", "schedule": timedelta(hours=24)},
    "refresh-whatsapp-templates": {"task": "refresh_whatsapp_templates", "schedule": timedelta(seconds=900)},
    # "resume_failed_tasks": {"task": "resume_failed_tasks", "schedule": timedelta(seconds=1800)},
}

# Mapping of task name to task function path, used when CELERY_ALWAYS_EAGER is set to True
CELERY_TASK_MAP = {"send_msg_task": "temba.channels.tasks.send_msg_task"}

# -----------------------------------------------------------------------------------
# Async tasks with celery
# -----------------------------------------------------------------------------------
REDIS_HOST = "localhost"
REDIS_PORT = 6379

# we use a redis db of 10 for testing so that we maintain caches for dev
REDIS_DB = 10 if TESTING else 15

BROKER_URL = "redis://%s:%d/%d" % (REDIS_HOST, REDIS_PORT, REDIS_DB)

# by default, celery doesn't have any timeout on our redis connections, this fixes that
BROKER_TRANSPORT_OPTIONS = {"socket_timeout": 5}

CELERY_RESULT_BACKEND = None
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"

IS_PROD = False
HOSTNAME = "localhost"

# The URL and port of the proxy server to use when needed (if any, in requests format)
OUTGOING_PROXIES = {}

# -----------------------------------------------------------------------------------
# Cache to Redis
# -----------------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://%s:%s/%s" % (REDIS_HOST, REDIS_PORT, REDIS_DB),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

# -----------------------------------------------------------------------------------
# Django-rest-framework configuration
# -----------------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "temba.api.support.APISessionAuthentication",
        "temba.api.support.APITokenAuthentication",
        "temba.api.support.APIBasicAuthentication",
    ),
    "DEFAULT_THROTTLE_CLASSES": ("temba.api.support.OrgUserRateThrottle",),
    "DEFAULT_THROTTLE_RATES": {
        "v2": "2500/hour",
        "v2.contacts": "2500/hour",
        "v2.messages": "2500/hour",
        "v2.broadcasts": "36000/hour",
        "v2.runs": "2500/hour",
        "v2.api": "2500/hour",
    },
    "PAGE_SIZE": 250,
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "DEFAULT_RENDERER_CLASSES": ("temba.api.support.DocumentationRenderer", "rest_framework.renderers.JSONRenderer"),
    "EXCEPTION_HANDLER": "temba.api.support.temba_exception_handler",
    "UNICODE_JSON": False,
    "STRICT_JSON": False,
}
REST_HANDLE_EXCEPTIONS = not TESTING

# -----------------------------------------------------------------------------------
# Django Compressor configuration
# -----------------------------------------------------------------------------------

if TESTING:
    # if only testing, disable coffeescript and less compilation
    COMPRESS_PRECOMPILERS = ()
else:
    COMPRESS_PRECOMPILERS = (
        ("text/less", 'lessc --include-path="%s" {infile} {outfile}' % os.path.join(PROJECT_DIR, "../static", "less")),
        ("text/coffeescript", "coffee --compile --stdio"),
    )

COMPRESS_ENABLED = False
COMPRESS_OFFLINE = False

# build up our offline compression context based on available brands
COMPRESS_OFFLINE_CONTEXT = []
for brand in BRANDING.values():
    context = dict(STATIC_URL=STATIC_URL, base_template="frame.html", debug=False, testing=False)
    context["brand"] = dict(slug=brand["slug"], styles=brand["styles"])
    COMPRESS_OFFLINE_CONTEXT.append(context)

# -----------------------------------------------------------------------------------
# RapidPro configuration settings
# -----------------------------------------------------------------------------------

######
# DANGER: only turn this on if you know what you are doing!
#         could cause messages to be sent to live customer aggregators
SEND_MESSAGES = False

######
# DANGER: only turn this on if you know what you are doing!
#         could cause emails to be sent in test environment
SEND_EMAILS = False

CLASSIFIER_TYPES = [
    "temba.classifiers.types.wit.WitType",
    "temba.classifiers.types.luis.LuisType",
    "temba.classifiers.types.bothub.BothubType",
]

CHANNEL_TYPES = [
    "temba.channels.types.arabiacell.ArabiaCellType",
    "temba.channels.types.whatsapp.WhatsAppType",
    "temba.channels.types.twilio.TwilioType",
    "temba.channels.types.twilio_whatsapp.TwilioWhatsappType",
    "temba.channels.types.twilio_messaging_service.TwilioMessagingServiceType",
    "temba.channels.types.signalwire.SignalWireType",
    "temba.channels.types.nexmo.NexmoType",
    "temba.channels.types.africastalking.AfricasTalkingType",
    "temba.channels.types.blackmyna.BlackmynaType",
    "temba.channels.types.bongolive.BongoLiveType",
    "temba.channels.types.burstsms.BurstSMSType",
    "temba.channels.types.chikka.ChikkaType",
    "temba.channels.types.clickatell.ClickatellType",
    "temba.channels.types.dartmedia.DartMediaType",
    "temba.channels.types.dmark.DMarkType",
    "temba.channels.types.external.ExternalType",
    "temba.channels.types.facebook.FacebookType",
    "temba.channels.types.firebase.FirebaseCloudMessagingType",
    "temba.channels.types.freshchat.FreshChatType",
    "temba.channels.types.globe.GlobeType",
    "temba.channels.types.highconnection.HighConnectionType",
    "temba.channels.types.hormuud.HormuudType",
    "temba.channels.types.hub9.Hub9Type",
    "temba.channels.types.infobip.InfobipType",
    "temba.channels.types.jasmin.JasminType",
    "temba.channels.types.jiochat.JioChatType",
    "temba.channels.types.junebug.JunebugType",
    "temba.channels.types.kannel.KannelType",
    "temba.channels.types.line.LineType",
    "temba.channels.types.m3tech.M3TechType",
    "temba.channels.types.macrokiosk.MacrokioskType",
    "temba.channels.types.mtarget.MtargetType",
    "temba.channels.types.mblox.MbloxType",
    "temba.channels.types.messangi.MessangiType",
    "temba.channels.types.novo.NovoType",
    "temba.channels.types.playmobile.PlayMobileType",
    "temba.channels.types.plivo.PlivoType",
    "temba.channels.types.redrabbit.RedRabbitType",
    "temba.channels.types.shaqodoon.ShaqodoonType",
    "temba.channels.types.smscentral.SMSCentralType",
    "temba.channels.types.start.StartType",
    "temba.channels.types.telegram.TelegramType",
    "temba.channels.types.thinq.ThinQType",
    "temba.channels.types.twiml_api.TwimlAPIType",
    "temba.channels.types.twitter.TwitterType",
    "temba.channels.types.twitter_legacy.TwitterLegacyType",
    "temba.channels.types.verboice.VerboiceType",
    "temba.channels.types.viber_public.ViberPublicType",
    "temba.channels.types.vk.VKType",
    "temba.channels.types.wavy.WavyType",
    "temba.channels.types.wechat.WeChatType",
    "temba.channels.types.yo.YoType",
    "temba.channels.types.zenvia.ZenviaType",
    "temba.channels.types.i2sms.I2SMSType",
    "temba.channels.types.clicksend.ClickSendType",
    "temba.channels.types.android.AndroidType",
]

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

SEGMENT_IO_KEY = os.environ.get("SEGMENT_IO_KEY", "")

INTERCOM_TOKEN = os.environ.get("INTERCOM_TOKEN", "")

LIBRATO_USER = os.environ.get("LIBRATO_USER", "")
LIBRATO_TOKEN = os.environ.get("LIBRATO_TOKEN", "")

# -----------------------------------------------------------------------------------
# IP Addresses
# These are the externally accessible IP addresses of the servers running RapidPro.
# Needed for channel types that authenticate by whitelisting public IPs.
#
# You need to change these to real addresses to work with these.
# -----------------------------------------------------------------------------------
IP_ADDRESSES = ("172.16.10.10", "162.16.10.20")

# -----------------------------------------------------------------------------------
# Installs may choose how big they want their text messages and contact fields to be.
# -----------------------------------------------------------------------------------
MSG_FIELD_SIZE = 640
VALUE_FIELD_SIZE = 640
FLOW_START_PARAMS_SIZE = 256

# -----------------------------------------------------------------------------------
# Installs may choose how long to keep the channel logs in hours
# by default we keep success logs for 48 hours and error_logs for 30 days(30 * 24 hours)
# Falsy values to keep the logs forever
# -----------------------------------------------------------------------------------
SUCCESS_LOGS_TRIM_TIME = 48
ALL_LOGS_TRIM_TIME = 24 * 30

# -----------------------------------------------------------------------------------
# Installs can also choose how long to keep EventFires around. By default this is
# 90 days which fits in nicely with the default archiving behavior.
# -----------------------------------------------------------------------------------
EVENT_FIRE_TRIM_DAYS = 90

# -----------------------------------------------------------------------------------
# Installs can also choose how long to keep FlowSessions around. These are
# potentially big but really helpful for debugging. Default is 7 days.
# -----------------------------------------------------------------------------------
FLOW_SESSION_TRIM_DAYS = 7

# -----------------------------------------------------------------------------------
# Mailroom - disabled by default, but is where simulation happens
# -----------------------------------------------------------------------------------
MAILROOM_URL = None
MAILROOM_AUTH_TOKEN = None

# To allow manage fields to support up to 1000 fields
DATA_UPLOAD_MAX_NUMBER_FIELDS = 4000

# When reporting metrics we use the hostname of the physical machine, not the hostname of the service
MACHINE_HOSTNAME = socket.gethostname().split(".")[0]


# ElasticSearch configuration (URL RFC-1738)
ELASTICSEARCH_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")

# Maximum active objects are org can have
MAX_ACTIVE_CONTACTFIELDS_PER_ORG = 255
MAX_ACTIVE_GLOBALS_PER_ORG = 255
