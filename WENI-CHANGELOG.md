1.2.5-rapidpro-7.0.4
----------
 * Helphero integration
 * New Sample Flow

1.2.4-rapidpro-7.0.4
----------
 * New Weni announcement
 * Option to disable OIDC Authentication

1.2.3-rapidpro-7.0.4
----------
 * Update weni-rp-apps to 1.0.16

1.2.2-rapidpro-7.0.4
----------
 * Update weni-rp-apps to 1.0.15
 * Change twilioflex form labels and help text

1.2.1-rapidpro-7.0.4
----------
 * Change refresh templates task schedule to 1h

1.2.0-rapidpro-7.0.4
----------
 * Add ticketer Twilio Flex

1.1.8-rapidpro-7.0.4
----------
 * Added option to exclude channels from claim view when not in the Weni Flows design

1.1.7-rapidpro-7.0.4
----------
 * Added LogRocket in place of Hotjar

1.1.6-rapidpro-7.0.4
----------
 * Add subscribed_apps permission for Instagram Channel
 * Feature to limit recovery password to 5 times in 12 hours.

1.1.5-rapidpro-7.0.4
----------
 * Update weni-rp-apps to 1.0.13
 * Add pages_read_engagement permission for Instagram
 * Fix: back button history behavior 
 * Downgrade elasticsearch version to 7.13.4

1.1.4-rapidpro-7.0.4
----------
 * Update weni-rp-apps to 1.0.12

1.1.3-rapidpro-7.0.4
----------
 * Fix: Use original filename instead uuid at end of path on upload attachment

1.1.2-rapidpro-7.0.4
----------
 * Fix: Branding with privacy url

1.1.1-rapidpro-7.0.4
----------
 * Fix: Kyrgyzstan whatsapp language code

1.1.0-rapidpro-7.0.4
----------
 * Merge instagram channel
 * Lock weni-rp-apps to 1.0.8
 * Feat/back button missing

1.0.7-rapidpro-7.0.4
----------
 * Fix: Gujarati whatsapp language code

1.0.6-rapidpro-7.0.4
----------
 * Add background flow type, location support and title variables in branding info.

1.0.5-rapidpro-7.0.4
----------
 * set NON_ISO6391_LANGUAGES on temba/settings.py.prod

1.0.4-rapidpro-7.0.4
----------
 * fixed CELERY_BROKER_URL

1.0.3-rapidpro-7.0.4
----------
 * DATABASES["readonly"] setting fixed

1.0.2-rapidpro-7.0.4
----------
 * Removed old two-factor authentication

1.0.1-rapidpro-7.0.4
----------
* RapidPro updated to v7.0.4
  * Settings updated on temba/settings.py.prod
    * LOG_TRIM_* variables replaced by RETENTION_PERIODS
    * CELERYBEAT_SCHEDULE updated to CELERY_BEAT_SCHEDULE
* Python updated to 3.9

1.0.1-rapidpro-6.5.15
----------
 * Downgrade psycopg2-binary version to 2.8.6 
 * Removes discontinued elasticapm processor 
 * Add weni-rp-apps to pip dependencies

1.0.0-rapidpro-6.5.15
----------
 * RapidPro v6.5.15
 * Dockerfile with python 3.6 and NodeJS 12
 * Dockerfile with Varnish 6.0 for static files caching
 * Added APM
 * Added log config
 * Allowed 32MB of client body size
 * Added a new env var to enable/disable existing loggers
 * Added weni-rp-apps into INSTALLED_APPS
 * Added Custom layout for use within Weni Connect
 * Enabled authentication from OIDC
 * Enabled CSP configuration
 * Removed Authorization from webhookresult and channellog requests on read
 * Added communication with Weni Connect via PostMessage
