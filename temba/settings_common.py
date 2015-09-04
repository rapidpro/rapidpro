import sys, os
from hamlpy import templatize

#-----------------------------------------------------------------------------------
# Sets TESTING to True if this configuration is read during a unit test
#-----------------------------------------------------------------------------------
TESTING = sys.argv[1:2] == ['test']

#-----------------------------------------------------------------------------------
# Default to debugging
#-----------------------------------------------------------------------------------
DEBUG = True
TEMPLATE_DEBUG = DEBUG

ADMINS = (
    ('RapidPro', 'code@yourdomain.io'),
)
MANAGERS = ADMINS

# hardcode the postgis version so we can do reset db's from a blank database
POSTGIS_VERSION = (2, 1)

#-----------------------------------------------------------------------------------
# set the mail settings, override these in your settings.py
# if your site was at http://temba.io, it might look like this:
#-----------------------------------------------------------------------------------
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_HOST_USER = 'server@temba.io'
DEFAULT_FROM_EMAIL = 'server@temba.io'
EMAIL_HOST_PASSWORD = 'mypassword'
EMAIL_USE_TLS = True

# where recordings and exports are stored
AWS_STORAGE_BUCKET_NAME = 'dl.temba.io'
STORAGE_ROOT_DIR = 'test_orgs' if TESTING else 'orgs'

#-----------------------------------------------------------------------------------
# On Unix systems, a value of None will cause Django to use the same
# timezone as the operating system.
# If running in a Windows environment this must be set to the same as your
# system time zone
#-----------------------------------------------------------------------------------
USE_TZ = True
TIME_ZONE = 'GMT'
USER_TIME_ZONE = 'Africa/Kigali'

MODELTRANSLATION_TRANSLATION_REGISTRY = "translation"

#-----------------------------------------------------------------------------------
# Default language used for this installation
#-----------------------------------------------------------------------------------
LANGUAGE_CODE = 'en-us'

#-----------------------------------------------------------------------------------
# Available languages for translation
#-----------------------------------------------------------------------------------
gettext = lambda s: s
LANGUAGES = (
    ('en-us', gettext("English")),
    ('pt-br', gettext("Portuguese")),
    ('fr', gettext("French")),
    ('es', gettext("Spanish")))

DEFAULT_LANGUAGE = "en-us"
DEFAULT_SMS_LANGUAGE = "en-us"

SITE_ID = 1

# If you set this to False, Django will make some optimizations so as not
# to load the internationalization machinery.
USE_I18N = True

# If you set this to False, Django will not format dates, numbers and
# calendars according to the current locale
USE_L10N = True

# Absolute filesystem path to the directory that will hold user-uploaded files.
# Example: "/home/media/media.lawrence.com/media/"
MEDIA_ROOT = ''

# URL that handles the media served from MEDIA_ROOT. Make sure to use a
# trailing slash.
# Examples: "http://media.lawrence.com/media/", "http://example.com/media/"
MEDIA_URL = ''

# Absolute path to the directory static files should be collected to.
# Don't put anything in this directory yourself; store your static files
# in apps' "static/" subdirectories and in STATICFILES_DIRS.
# Example: "/home/media/media.lawrence.com/static/"
STATIC_ROOT = ''

# URL prefix for static files.
# Example: "http://media.lawrence.com/static/"
STATIC_URL = '/static/'

# URL prefix for admin static files -- CSS, JavaScript and images.
# Make sure to use a trailing slash.
# Examples: "http://foo.com/static/admin/", "/static/admin/".
ADMIN_MEDIA_PREFIX = '/static/admin/'

# Additional locations of static files
STATICFILES_DIRS = (
    # Put strings here, like "/home/html/static" or "C:/www/django/static".
    # Always use forward slashes, even on Windows.
    # Don't forget to use absolute paths, not relative paths.
)

# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    'compressor.finders.CompressorFinder',
)

# Make this unique, and don't share it with anybody.
SECRET_KEY = 'your own secret key'

# List of callables that know how to import templates from various sources.
TEMPLATE_LOADERS = (
    'hamlpy.template.loaders.HamlPyFilesystemLoader',
    'hamlpy.template.loaders.HamlPyAppDirectoriesLoader',
    'django.template.loaders.filesystem.Loader',
    'django.template.loaders.app_directories.Loader',
    'django.template.loaders.eggs.Loader',
)

EMAIL_CONTEXT_PROCESSORS = ('temba.temba_email.link_components',)

TEMPLATE_CONTEXT_PROCESSORS = (
    'django.contrib.auth.context_processors.auth',
    'django.core.context_processors.debug',
    'django.core.context_processors.i18n',
    'django.core.context_processors.media',
    'django.core.context_processors.static',
    'django.contrib.messages.context_processors.messages',
    'django.core.context_processors.request',
    'temba.context_processors.branding',
    'temba.orgs.context_processors.user_group_perms_processor',
    'temba.orgs.context_processors.unread_count_processor',
    'temba.channels.views.channel_status_processor',
    'temba.msgs.views.send_message_auto_complete_processor',
    'temba.api.views.webhook_status_processor',
    'temba.orgs.context_processors.settings_includer',
)

if TESTING:
    TEMPLATE_CONTEXT_PROCESSORS += ('temba.tests.add_testing_flag_to_context', )

MIDDLEWARE_CLASSES = (
    'django.middleware.common.CommonMiddleware',
    'temba.utils.middleware.DisableMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'temba.middleware.BrandingMiddleware',
    'temba.middleware.OrgTimezoneMiddleware',
    'temba.middleware.FlowSimulationMiddleware',
    'temba.middleware.ActivateLanguageMiddleware',
    'temba.middleware.NonAtomicGetsMiddleware',
)

ROOT_URLCONF = 'temba.urls'

# other urls to add
APP_URLS = []

SITEMAP = ('public.public_index', 'public.video_list', 'public.public_blog',
           'api', 'api.explorer', 'api.webhook', 'api.webhook_simulator',
           'api.sms', 'api.flows', 'api.runs', 'api.calls', 'api.channels')

INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'django.contrib.gis',

    # django sitemaps
    'django.contrib.sitemaps',

    'redis',

    # mo-betta permission management
    'guardian',

    # rest framework for api access
    'rest_framework',
    'rest_framework.authtoken',

    # compress our CSS and js
    'compressor',

    # smartmin
    'smartmin',

    # import tasks
    'smartmin.csv_imports',

    # smartmin users
    'smartmin.users',
    'modeltranslation',

    # async tasks,
    'djcelery',

    # django-timezones
    'timezones',

    # sentry
    'raven.contrib.django',
    'raven.contrib.django.celery',

    # temba apps
    'temba.assets',
    'temba.auth_tweaks',
    'temba.api',
    'temba.public',
    'temba.schedules',
    'temba.orgs',
    'temba.contacts',
    'temba.channels',
    'temba.msgs',
    'temba.flows',
    'temba.reports',
    'temba.triggers',
    'temba.utils',
    'temba.campaigns',
    'temba.ivr',
    'temba.locations',
    'temba.values',
)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'root': {
        'level': 'WARNING',
        'handlers': ['console'],
    },
    'formatters': {
        'verbose': {
            'format': '%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s'
        },
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose'
        }
    },
    'loggers': {
        'pycountry': {
            'level': 'ERROR',
            'handlers': ['console'],
            'propagate': False,
        },
        'django.db.backends': {
            'level': 'ERROR',
            'handlers': ['console'],
            'propagate': False,
        },
    },
}

#-----------------------------------------------------------------------------------
# Branding Configuration
#-----------------------------------------------------------------------------------
from django.utils.translation import ugettext_lazy as _
BRANDING = {
    'rapidpro.io': {
        'slug': 'rapidpro',
        'name': 'RapidPro',
        'org': 'UNICEF',
        'styles': ['brands/rapidpro/font/style.css', 'brands/rapidpro/less/style.less'],
        'welcome_topup': 1000,
        'email': 'join@rapidpro.io',
        'support_email': 'support@rapidpro.io',
        'link': 'https://app.rapidpro.io',
        'api_link': 'https://api.rapidpro.io',
        'docs_link': 'http://knowledge.rapidpro.io',
        'domain': 'app.rapidpro.io',
        'favico': 'brands/rapidpro/rapidpro.ico',
        'splash': '/brands/rapidpro/splash.jpg',
        'logo': '/brands/rapidpro/logo.png',
        'allow_signups': True,
        'welcome_packs': [dict(size=5000, name="Demo Account"), dict(size=100000, name="UNICEF Account")],
        'description': _("Visually build nationally scalable mobile applications from anywhere in the world."),
        'credits': _("Copyright &copy; 2012-2015 UNICEF, Nyaruka. All Rights Reserved.")
    }
}
DEFAULT_BRAND = 'rapidpro.io'

#-----------------------------------------------------------------------------------
# Directory Configuration
#-----------------------------------------------------------------------------------
PROJECT_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)))
LOCALE_PATHS = (os.path.join(PROJECT_DIR, '../locale'),)
RESOURCES_DIR = os.path.join(PROJECT_DIR, '../resources')
FIXTURE_DIRS = (os.path.join(PROJECT_DIR, '../fixtures'),)
TESTFILES_DIR = os.path.join(PROJECT_DIR, '../testfiles')
TEMPLATE_DIRS = (os.path.join(PROJECT_DIR, '../templates'),)
STATICFILES_DIRS = (os.path.join(PROJECT_DIR, '../static'), os.path.join(PROJECT_DIR, '../media'), )
STATIC_ROOT = os.path.join(PROJECT_DIR, '../sitestatic')
COMPRESS_ROOT = os.path.join(PROJECT_DIR, '../sitestatic')
MEDIA_ROOT = os.path.join(PROJECT_DIR, '../media')
MEDIA_URL = "/media/"

#-----------------------------------------------------------------------------------
# Permission Management
#-----------------------------------------------------------------------------------

# this lets us easily create new permissions across our objects
PERMISSIONS = {
    '*': ('create',  # can create an object
          'read',    # can read an object, viewing it's details
          'update',  # can update an object
          'delete',  # can delete an object,
          'list'),   # can view a list of the objects

    'campaigns.campaign': ('api',
                           'archived',
                           ),

    'campaigns.campaignevent': ('api',),


    'contacts.contact': ('api',
                         'block',
                         'blocked',
                         'customize',
                         'export',
                         'failed',
                         'filter',
                         'history',
                         'import',
                         'omnibox',
                         'unblock',
                         ),

    'contacts.contactfield': ('api',
                              'json',
                              'managefields'),

    'contacts.contactgroup': ('api',),

    'ivr.ivrcall': ('start',),

    'locations.adminboundary': ('alias',
                                'api',
                                'boundaries',
                                'geometry'),

    'orgs.org': ('country',
                 'clear_cache',
                 'create_login',
                 'download',
                 'edit',
                 'export',
                 'grant',
                 'home',
                 'import',
                 'join',
                 'languages',
                 'manage',
                 'manage_accounts',
                 'nexmo_account',
                 'nexmo_connect',
                 'plivo_connect',
                 'profile',
                 'service',
                 'signup',
                 'trial',
                 'twilio_account',
                 'twilio_connect',
                 'webhook',
                 ),

    'orgs.usersettings': ('phone',),


    'channels.channel': ('api',
                         'bulk_sender_options',
                         'claim',
                         'claim_africas_talking',
                         'claim_android',
                         'claim_blackmyna',
                         'claim_clickatell',
                         'claim_external',
                         'claim_high_connection',
                         'claim_hub9',
                         'claim_infobip',
                         'claim_kannel',
                         'claim_nexmo',
                         'claim_plivo',
                         'claim_shaqodoon',
                         'claim_smscentral',
                         'claim_twilio',
                         'claim_twitter',
                         'claim_verboice',
                         'claim_vumi',
                         'claim_zenvia',
                         'configuration',
                         'create_bulk_sender',
                         'create_caller',
                         'errors',
                         'search_nexmo',
                         'search_numbers',
                         ),

    'flows.flow': ('activity',
                   'activity_list',
                   'analytics',
                   'api',
                   'archived',
                   'broadcast',
                   'completion',
                   'copy',
                   'editor',
                   'export',
                   'export_results',
                   'filter',
                   'json',
                   'read',
                   'recent_messages',
                   'results',
                   'simulate',
                   'upload_action_recording',
                   'versions',
                   ),

    'flows.ruleset': ('analytics',
                      'choropleth',
                      'map',
                      'results',
                      ),

    'msgs.msg': ('api',
                 'archive',
                 'archived',
                 'export',
                 'failed',
                 'filter',
                 'flow',
                 'inbox',
                 'label',
                 'outbox',
                 'sent',
                 'test',
                 'update',
                 ),

    'msgs.broadcast': ('api',
                       'detail',
                       'schedule',
                       'schedule_list',
                       'schedule_read',
                       'send',
                       ),

    'msgs.call': ('api',),

    'msgs.label': ('api', 'create', 'create_folder'),

    'orgs.topup': ('manage',),

    'triggers.trigger': ('archived',
                         'catchall',
                         'follow',
                         'inbound_call',
                         'keyword',
                         'missed_call',
                         'register',
                         'schedule',
                         ),
}

# assigns the permissions that each group should have
GROUP_PERMISSIONS = {
    "Service Users": (  # internal Temba services have limited permissions
        'msgs.msg_create',
    ),
    "Alpha": (
    ),
    "Beta": (
    ),
    "Granters": (
        'orgs.org_grant',
    ),
    "Customer Support": (
        'auth.user_list',
        'auth.user_update',
        'flows.flow_editor',
        'flows.flow_json',
        'flows.flow_read',
        'flows.flow_versions',
        'orgs.org_dashboard',
        'orgs.org_grant',
        'orgs.org_manage',
        'orgs.org_update',
        'orgs.org_service',
        'orgs.topup_create',
        'orgs.topup_manage',
        'orgs.topup_update',
    ),
    "Administrators": (
        'api.webhookevent_list',
        'api.webhookevent_read',

        'campaigns.campaign.*',
        'campaigns.campaignevent.*',

        'contacts.contact_api',
        'contacts.contact_block',
        'contacts.contact_blocked',
        'contacts.contact_create',
        'contacts.contact_customize',
        'contacts.contact_delete',
        'contacts.contact_export',
        'contacts.contact_failed',
        'contacts.contact_filter',
        'contacts.contact_history',
        'contacts.contact_import',
        'contacts.contact_list',
        'contacts.contact_omnibox',
        'contacts.contact_read',
        'contacts.contact_unblock',
        'contacts.contact_update',
        'contacts.contactfield.*',
        'contacts.contactgroup.*',

        'csv_imports.importtask.*',

        'ivr.ivrcall.*',

        'locations.adminboundary_alias',
        'locations.adminboundary_api',
        'locations.adminboundary_boundaries',
        'locations.adminboundary_geometry',

        'orgs.org_country',
        'orgs.org_download',
        'orgs.org_edit',
        'orgs.org_export',
        'orgs.org_home',
        'orgs.org_import',
        'orgs.org_languages',
        'orgs.org_manage_accounts',
        'orgs.org_nexmo_account',
        'orgs.org_nexmo_connect',
        'orgs.org_plivo_connect',
        'orgs.org_profile',
        'orgs.org_twilio_account',
        'orgs.org_twilio_connect',
        'orgs.org_webhook',
        'orgs.topup_list',
        'orgs.topup_read',
        'orgs.usersettings_phone',
        'orgs.usersettings_update',

        'channels.channel_claim_nexmo',
        'channels.channel_api',
        'channels.channel_bulk_sender_options',
        'channels.channel_claim',
        'channels.channel_claim_africas_talking',
        'channels.channel_claim_android',
        'channels.channel_claim_blackmyna',
        'channels.channel_claim_clickatell',
        'channels.channel_claim_external',
        'channels.channel_claim_high_connection',
        'channels.channel_claim_hub9',
        'channels.channel_claim_infobip',
        'channels.channel_claim_kannel',
        'channels.channel_claim_plivo',
        'channels.channel_claim_shaqodoon',
        'channels.channel_claim_smscentral',
        'channels.channel_claim_twilio',
        'channels.channel_claim_twitter',
        'channels.channel_claim_verboice',
        'channels.channel_claim_vumi',
        'channels.channel_claim_zenvia',
        'channels.channel_configuration',
        'channels.channel_create',
        'channels.channel_create_bulk_sender',
        'channels.channel_create_caller',
        'channels.channel_delete',
        'channels.channel_list',
        'channels.channel_read',
        'channels.channel_search_nexmo',
        'channels.channel_search_numbers',
        'channels.channel_update',
        'channels.channellog_list',
        'channels.channellog_read',

        'reports.report.*',

        'flows.flow.*',
        'flows.flowlabel.*',
        'flows.ruleset.*',

        'schedules.schedule.*',

        'msgs.broadcast.*',
        'msgs.broadcastschedule.*',
        'msgs.call.*',
        'msgs.label.*',
        'msgs.msg_api',
        'msgs.msg_archive',
        'msgs.msg_archived',
        'msgs.msg_delete',
        'msgs.msg_export',
        'msgs.msg_failed',
        'msgs.msg_filter',
        'msgs.msg_flow',
        'msgs.msg_inbox',
        'msgs.msg_label',
        'msgs.msg_outbox',
        'msgs.msg_sent',
        'msgs.msg_update',

        'triggers.trigger.*',

    ),
    "Editors": (
        'api.webhookevent_list',
        'api.webhookevent_read',

        'campaigns.campaign.*',
        'campaigns.campaignevent.*',

        'contacts.contact_api',
        'contacts.contact_block',
        'contacts.contact_blocked',
        'contacts.contact_create',
        'contacts.contact_customize',
        'contacts.contact_delete',
        'contacts.contact_export',
        'contacts.contact_failed',
        'contacts.contact_filter',
        'contacts.contact_history',
        'contacts.contact_import',
        'contacts.contact_list',
        'contacts.contact_omnibox',
        'contacts.contact_read',
        'contacts.contact_unblock',
        'contacts.contact_update',
        'contacts.contactfield.*',
        'contacts.contactgroup.*',

        'csv_imports.importtask.*',

        'ivr.ivrcall.*',

        'locations.adminboundary_alias',
        'locations.adminboundary_api',
        'locations.adminboundary_boundaries',
        'locations.adminboundary_geometry',

        'orgs.org_download',
        'orgs.org_export',
        'orgs.org_home',
        'orgs.org_import',
        'orgs.org_profile',
        'orgs.org_webhook',
        'orgs.topup_list',
        'orgs.topup_read',
        'orgs.usersettings_phone',
        'orgs.usersettings_update',

        'channels.channel_api',
        'channels.channel_bulk_sender_options',
        'channels.channel_claim',
        'channels.channel_claim_africas_talking',
        'channels.channel_claim_android',
        'channels.channel_claim_blackmyna',
        'channels.channel_claim_clickatell',
        'channels.channel_claim_external',
        'channels.channel_claim_high_connection',
        'channels.channel_claim_hub9',
        'channels.channel_claim_infobip',
        'channels.channel_claim_kannel',
        'channels.channel_claim_plivo',
        'channels.channel_claim_shaqodoon',
        'channels.channel_claim_smscentral',
        'channels.channel_claim_twilio',
        'channels.channel_claim_twitter',
        'channels.channel_claim_verboice',
        'channels.channel_claim_vumi',
        'channels.channel_claim_zenvia',
        'channels.channel_configuration',
        'channels.channel_create',
        'channels.channel_create_bulk_sender',
        'channels.channel_create_caller',
        'channels.channel_delete',
        'channels.channel_list',
        'channels.channel_read',
        'channels.channel_search_numbers',
        'channels.channel_update',

        'reports.report.*',

        'flows.flow.*',
        'flows.flowlabel.*',
        'flows.ruleset.*',

        'schedules.schedule.*',

        'msgs.broadcast.*',
        'msgs.broadcastschedule.*',
        'msgs.call.*',
        'msgs.label.*',
        'msgs.msg_api',
        'msgs.msg_archive',
        'msgs.msg_archived',
        'msgs.msg_delete',
        'msgs.msg_export',
        'msgs.msg_failed',
        'msgs.msg_filter',
        'msgs.msg_flow',
        'msgs.msg_inbox',
        'msgs.msg_label',
        'msgs.msg_outbox',
        'msgs.msg_sent',
        'msgs.msg_update',

        'triggers.trigger.*',

    ),
    "Viewers": (
        'campaigns.campaign_archived',
        'campaigns.campaign_list',
        'campaigns.campaign_read',
        'campaigns.campaignevent_read',

        'contacts.contact_blocked',
        'contacts.contact_export',
        'contacts.contact_failed',
        'contacts.contact_filter',
        'contacts.contact_history',
        'contacts.contact_list',
        'contacts.contact_read',

        'locations.adminboundary_boundaries',
        'locations.adminboundary_geometry',
        'locations.adminboundary_alias',

        'orgs.org_download',
        'orgs.org_export',
        'orgs.org_home',
        'orgs.org_profile',
        'orgs.topup_list',
        'orgs.topup_read',

        'channels.channel_list',
        'channels.channel_read',

        'flows.flow_activity',
        'flows.flow_archived',
        'flows.flow_completion',
        'flows.flow_export',
        'flows.flow_export_results',
        'flows.flow_filter',
        'flows.flow_list',
        'flows.flow_read',
        'flows.flow_editor',
        'flows.flow_json',
        'flows.flow_recent_messages',
        'flows.flow_results',
        'flows.flow_simulate',
        'flows.ruleset_analytics',
        'flows.ruleset_results',
        'flows.ruleset_map',
        'flows.ruleset_choropleth',

        'msgs.broadcast_schedule_list',
        'msgs.broadcast_schedule_read',
        'msgs.call_list',
        'msgs.msg_archived',
        'msgs.msg_export',
        'msgs.msg_failed',
        'msgs.msg_filter',
        'msgs.msg_flow',
        'msgs.msg_inbox',
        'msgs.msg_outbox',
        'msgs.msg_sent',

        'triggers.trigger_archived',
        'triggers.trigger_list',
    )
}

#-----------------------------------------------------------------------------------
# Login / Logout
#-----------------------------------------------------------------------------------
LOGIN_URL = "/users/login/"
LOGOUT_URL = "/users/logout/"
LOGIN_REDIRECT_URL = "/org/choose/"
LOGOUT_REDIRECT_URL = "/"

#-----------------------------------------------------------------------------------
# Guardian Configuration
#-----------------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = (
    'smartmin.backends.CaseInsensitiveBackend',
    'guardian.backends.ObjectPermissionBackend',
)

ANONYMOUS_USER_ID = -1

#-----------------------------------------------------------------------------------
# Async tasks with django-celery, for testing we use a memory test backend
#-----------------------------------------------------------------------------------
BROKER_BACKEND = 'memory'

#-----------------------------------------------------------------------------------
# Django-Nose config
#-----------------------------------------------------------------------------------
TEST_RUNNER = 'django_nose.NoseTestSuiteRunner'

#-----------------------------------------------------------------------------------
# Debug Toolbar
#-----------------------------------------------------------------------------------
import iptools
INTERNAL_IPS = iptools.IpRangeList(
    '127.0.0.1',
    '192.168.0.10',
    '192.168.0.0/24',  # network block
    '0.0.0.0'
)

DEBUG_TOOLBAR_CONFIG = {
    'INTERCEPT_REDIRECTS': False,  # disable redirect traps
}

#-----------------------------------------------------------------------------------
# Crontab Settings ..
#-----------------------------------------------------------------------------------

from datetime import timedelta
from celery.schedules import crontab

CELERYBEAT_SCHEDULE = {
    "retry-webhook-events": {
        'task': 'retry_events_task',
        'schedule': timedelta(seconds=300),
    },
    "check-channels": {
        'task': 'check_channels_task',
        'schedule': timedelta(seconds=300),
    },
    "schedules": {
        'task': 'check_schedule_task',
        'schedule': timedelta(seconds=60),
    },
    "campaigns": {
        'task': 'check_campaigns_task',
        'schedule': timedelta(seconds=60),
    },
    "check-flows": {
        'task': 'check_flows_task',
        'schedule': timedelta(seconds=60),
    },
    "check-credits": {
        'task': 'check_credits_task',
        'schedule': timedelta(seconds=900)
    },
    "check-messages-task": {
        'task': 'check_messages_task',
        'schedule': timedelta(seconds=300)
    },
    "fail-old-messages": {
        'task': 'fail_old_messages',
        'schedule': crontab(hour=0, minute=0),
    },
    "trim-channel-log": {
        'task': 'trim_channel_log_task',
        'schedule': crontab(hour=3, minute=0),
    },
    "calculate-credit-caches": {
        'task': 'calculate_credit_caches',
        'schedule': timedelta(days=3),
    }
}

# Mapping of task name to task function path, used when CELERY_ALWAYS_EAGER is set to True
CELERY_TASK_MAP = {
    'send_msg_task': 'temba.channels.tasks.send_msg_task',
    'start_msg_flow_batch': 'temba.flows.tasks.start_msg_flow_batch_task',
    'handle_event_task': 'temba.msgs.tasks.handle_event_task',
}

#-----------------------------------------------------------------------------------
# Async tasks with django-celery
#-----------------------------------------------------------------------------------
import djcelery
djcelery.setup_loader()

REDIS_HOST = 'localhost'
REDIS_PORT = 6379

# we use a redis db of 10 for testing so that we maintain caches for dev
REDIS_DB = 10 if TESTING else 15

BROKER_URL = 'redis://%s:%d/%d' % (REDIS_HOST, REDIS_PORT, REDIS_DB)

# by default, celery doesn't have any timeout on our redis connections, this fixes that
BROKER_TRANSPORT_OPTIONS = {'socket_timeout': 5}

CELERY_RESULT_BACKEND = BROKER_URL

IS_PROD = False
HOSTNAME = "localhost"

# The URL and port of the proxy server to use when needed (if any, in requests format)
OUTGOING_PROXIES = {}

#-----------------------------------------------------------------------------------
# Cache to Redis
#-----------------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "redis_cache.cache.RedisCache",
        "LOCATION": "%s:%s:%s" % (REDIS_HOST, REDIS_PORT, REDIS_DB),
        "OPTIONS": {
            "CLIENT_CLASS": "redis_cache.client.DefaultClient",
        }
    }
}

#-----------------------------------------------------------------------------------
# Django-rest-framework configuration
#-----------------------------------------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework.authentication.SessionAuthentication',
        'temba.api.authentication.APITokenAuthentication',
    ),
    'PAGINATE_BY': 250,
    'DEFAULT_RENDERER_CLASSES': (
        'temba.api.renderers.DocumentationRenderer',
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.XMLRenderer',
    )
}

#-----------------------------------------------------------------------------------
# Aggregator settings
#-----------------------------------------------------------------------------------

# Hub9 is an aggregator in Indonesia, set this to the endpoint for your service
# and make sure you send from a whitelisted IP Address
HUB9_ENDPOINT = 'http://175.103.48.29:28078/testing/smsmt.php'

#-----------------------------------------------------------------------------------
# Django Compressor configuration
#-----------------------------------------------------------------------------------

COMPRESS_PRECOMPILERS = (
    ('text/less', 'lessc --include-path="%s" {infile} {outfile}' % os.path.join(PROJECT_DIR, '../static', 'less')),
    ('text/coffeescript', 'coffee --compile --stdio'))
COMPRESS_OFFLINE_CONTEXT = dict(STATIC_URL=STATIC_URL, base_template='frame.html')

COMPRESS_ENABLED = False
COMPRESS_OFFLINE = False
COMPRESS_URL = '/sitestatic/'

MAGE_API_URL = 'http://localhost:8026/api/v1'
MAGE_AUTH_TOKEN = '___MAGE_TOKEN_YOU_PICK__'

#-----------------------------------------------------------------------------------
# RapidPro configuration settings
#-----------------------------------------------------------------------------------

######
# DANGER: only turn this on if you know what you are doing!
#         could cause messages to be sent to live customer aggregators
SEND_MESSAGES = False

######
# DANGER: only turn this on if you know what you are doing!
#         could cause external APIs to be called in test environment
SEND_WEBHOOKS = False

######
# DANGER: only turn this on if you know what you are doing!
#         could cause emails to be sent in test environment
SEND_EMAILS = False

MESSAGE_HANDLERS = ['temba.triggers.handlers.TriggerHandler',
                    'temba.flows.handlers.FlowHandler',
                    'temba.triggers.handlers.CatchAllHandler']

#-----------------------------------------------------------------------------------
# Store sessions in our cache
#-----------------------------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_CACHE_ALIAS = "default"

#-----------------------------------------------------------------------------------
# 3rd Party Integration Keys
#-----------------------------------------------------------------------------------
TWITTER_API_KEY = os.environ.get('TWITTER_API_KEY', 'MISSING_TWITTER_API_KEY')
TWITTER_API_SECRET = os.environ.get('TWITTER_API_SECRET', 'MISSING_TWITTER_API_SECRET')

# SEGMENT_IO_KEY = "your segment.io key here"
