v3.0.45
----------
 * Fix absolute positioning for account creation form
 * Add Line channel icon in fonts
 * Add data migrations to update org config to connect to Nexmo

v3.0.43
----------
 * Add Malawi as a country for Africa's Talking

v3.0.42
----------
 * Widen pages to browser width so more can fit
 * Fix the display of URNs on contact list page
 * Fix searching of Nexmo number on connected accounts

v3.0.41
----------
 * Fix channel countries being duplicated for airtime configuration
 * Add make_sql command to generate SQL files for an app, reorganize current SQL reference files
 * Added SquashableModel and use it for all squashable count classes

v3.0.40
----------
 * Add support for Nexmo IVR
 * Log IVR interactions in Channel Log

v3.0.37
----------
 * Fix to make label of open ended response be All Response even if there is timeout on the ruleset
 * Data migration to rename category for old Values collected with timeouts

v3.0.36
----------
 * Add 256 keys to @extra, also enforce ordering so it is predictible which are included
 * Make fetching flow run stats more efficient and expose number of active runs on flow run endpoint
 * Migration to populate session on msg and ended_on where it is missing

v3.0.35
----------
 * Offline context per brand

v3.0.34
----------
 * Add Junebug channel type
 * Better base styling for dev project
 * Pass charset parameter to Kannel when sending unicode
 * Zero out minutes, seconds, ms for campaign events with set delivery horus
 * Add other URN types to contact context, return '' if missing, '*' mask for anon orgs
 * Make sure Campaigns export base_language for simple message events, honor on import

v3.0.33
----------
 * Change ansible command run on vagrant up from syncdb to migrate
 * Remove no longer needed django-modeltranslation
 * Keep up to 256 extra keys from webhooks instead of 128
 * Add documentation of API rate limiting

v3.0.32
----------
 * Make styling variables uniform across branding
 * Make brand styling optional

v3.0.28
----------
 * Add support for subflows over IVR

v3.0.27
----------
 * Fix searching for Twilio numbers, add unit tests
 * Fix API v1 run serialization when step messages are purged

v3.0.26
----------
 * Adds more substitutions from accented characters to gsm7 plain characters

v3.0.25
----------
 * Populate ended_on for ivr calls
 * Add session foreign key to Msg model

v3.0.24
----------
 * Fix bug in starting calls from sessions

v3.0.23
----------
 * Remove flow from ChannelSession, sessions can span many runs/flows
 * Remove superfluous channelsession.parent

v3.0.22
----------
 * Migration to update existing twiml apps with a status_callback, remove api/v1 references

v3.0.21
----------
 * Various tweaks to wording and presentation around custom SMTP email config

v3.0.20
----------
 * Allow orgs to set their own SMTP server for outgoing emails
 * Return better error message when To number not passed to Twilio handler
 * Exclude Flow webhook events from retries (we try once and forget)
 * Don't pass channel in webhook events if we don't know it
 * Use JsonResponse and response.json() consistently
 * Replace json.loads(response.content) with response.json() which properly decodes on Python 3

v3.0.19
----------
 * Improve performance of contact searches by location by fetching locations in separate query

v3.0.18
----------
 * Update pyparsing to 2.1.10
 * Update to new django-hamlpy
 * Display flow runs exits on the contact timeline
 * Fix Travis settings file for Python 3
 * Fix more Python 3 syntax issues
 * Fix RecentMessages no longer supporting requests with multiple rules, and add tests for that
 * Use print as function rather than statement for future Python 3 compatibility
 * Do not populate contact name for anon orgs from Viber
 * Add is_squashed to FlowPathCount and FlowRunCount
 * Updates to using boto3, if using AWS for storing imports or exports you'll need to change your settings file: `DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'`

v3.0.14
----------
 * Allow for the creation of Facebook referral triggers (opt-in on FB)
 * Allow for whitelisting of domains for Facebook channels

v3.0.13
----------
 * New contact field editing UI with Intercooler modals

v3.0.9
----------
 * Update RecentMessages view to use new recent messages model
 * Remove now unused indexes on FlowStep

v3.0.8
----------
 * Adds data migration to populate FlowPathRecentStep from existing Flow Steps

v3.0.7
----------
 * Introduce new model, FlowPathRecentStep that tracks recent steps from one flow node to another. This will replace the rather expensive index used to show recent flow activity on a flow path.

v3.0.10
----------
 * Log any exceptions encountered in Celery tasks to Raven
 * Tell user to get pages_messaging_subscriptions permission for their FB app

v3.0.6
----------
 * Replace unicode non breaking spaces with a normal space for GSM7 encoding (Kannel only)
 * Add migrations for custom indexes (existing installs before v3 should fake these)

v3.0.5
----------
 * fix styling on loader ball animation

v3.0.4
----------
 * Fix issue causing flow run table on flow dashboard to be very slow if a flow contained many responses

v3.0.3
----------
 * Refactor JSON responses to use native Django JSONResponse
 * Dont use proxy for Dart Media and Hub9, expose IPs to whitelist

v3.0.2
----------
 * Fixes DartMedia channel for short codes

v3.0.1
----------
 * Remove django-celery as it is unneeded, also stop saving Celery tombstones as we now store
   all task state (ContactImport for example) directly in models

v3.0.0
----------
 * IMPORTANT: This release resets all Temba migrations. You need to run the latest migrations
   from a version preceding this one, then fake all temba migrations when deploying:
```
% python manage.py migrate csv_imports
% python manage.py migrate airtime --fake
% python manage.py migrate api --fake
% python manage.py migrate campaigns --fake 
% python manage.py migrate channels --fake
% python manage.py migrate contacts --fake
% python manage.py migrate flows --fake
% python manage.py migrate ivr --fake
% python manage.py migrate locations --fake
% python manage.py migrate msgs --fake
% python manage.py migrate orgs --fake
% python manage.py migrate public --fake
% python manage.py migrate reports --fake
% python manage.py migrate schedules --fake
% python manage.py migrate triggers --fake
% python manage.py migrate ussd --fake
% python manage.py migrate values --fake
% python manage.py migrate
```
 * Django 1.10
 * Guardian 1.4.6
 * MPTT 0.8.7
 * Extensions 1.7.5
 * Boto 2.45.0
 * Django Storages 1.5.1
