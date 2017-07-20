v3.0.171
----------
 * Fixes for Twitter Activity channels
 * Add stop contact command to mage handler
 * Convert Firebase Cloud Messaging to a dynamic channel type
 * Convert Viber Public to a dynamic channel type
 * Change to the correct way for dynamic channel
 * Convert LINE to a dynamic channel type
 * Better message in SMS alert email

v3.0.170
----------
 * Hide SMTP config password and do not change the set password if blank is submitted
 * Validate the length of message campaigns for better user feedback
 * Make FlowRun.uuid unique and non-null (advise faking this and building index concurrently)

v3.0.169
----------
 * Migration to populate FlowRun.uuid. Advise faking this and running manually.
 * More channel logs for Jiochat channel interactions

v3.0.167
----------
 * Fix inclusion of attachment urls in webhook payloads and add tests
 * Install lxml to improve performance of large Excel exports
 * Add proper deactivation of Telegram channels
 * Converted Facebook and Telegram to dynamic channel types
 * Add nullable uuid field to FlowRun
 * Make sure we consider all URN schemes we can send to when looking up the if we have a send channel
 * Split Twitter and Twitter Beta into separate channel types
 * Remove support for old-style Twilio endpoints

v3.0.166
----------
 * Release channels before Twilio/Nexmo configs are cleared
 * Expose flow start UUID on runs from the runs endpoint

v3.0.165
----------
 * Migration to populate FlowStart.uuid on existing objects (advise faking and run manually)

v3.0.163
----------
 * Add uuid field to FlowStart
 * Migration to convert TwiML apps

v3.0.160
----------
 * Add support for Twitter channels using new beta Activity API

v3.0.159
----------
 * Clean incoming message text to remove invalid chars

v3.0.158
----------
 * Add more exception currencies for pycountry
 * Support channel specific Twilio endpoints

v3.0.156
----------
 * Clean up pip-requires and reset pip-freeze

v3.0.155
----------
 * Reduce the rate limit for SMS central to 1 requests per second
 * Display Jiochat on channel claim page
 * Fix date pickers on modal forms
 * Update channels to generate messages with multiple attachments

v3.0.154
----------
 * Rate limit sending throught SMS central to 10 messages per second
 * Fix some more uses of Context objects no longer supported in django 1.11
 * Fix channel log list request time display
 * Add @step.text and @step.attachments to message context

v3.0.153
----------
 * Jiochat channels
 * Django 1.11

v3.0.151
----------
 * Convert all squashable and prunable models to use big primary keys

v3.0.150
----------
 * Drop database-level length restrictions on msg and values
 * Add sender ID config for Macrokiosk channels
 * Expose org credit information on API org endpoint
 * Add contact_uuid parameter to update FCM user
 * Add configurable webhook header fields

v3.0.148
----------
* Fix simulator with attachments
* Switch to using new recent messages model

v3.0.147
----------
 * Migration to populate FlowPathRecentMessage
 * Clip messages to 640 chars for recent messages table

v3.0.145
----------
 * Change Macrokiosk time format to not have space
 * Better error message for external channel handler for wrong time format
 * Add new model for tracking recent messages on flow path segments

v3.0.144
----------
 * Remove Msg.media field that was replaced by Msg.attachments
 * Change default ivr timeouts to 2m
 * Fix the content-type for Twilio call response

v3.0.143
----------
 * Update contact read page and inbox views to show multiple message attachments 
 * Fix use of videojs to provide consistent video playback across browsers
 * API should return error message if user provides something unparseable for a non-serializer param

v3.0.142
----------
 * Fix handling of old msg structs with no attachments attribute
 * Tweak in create_outgoing to prevent possible NPEs in flow execution
 * Switch to using Msg.attachments instead of Msg.media
 * Replace index on Value.string_value with one that is limited to first 32 chars

v3.0.139
----------
* Fix Macrokiosk JSON responses

v3.0.138
----------
 * Migration to populate attachments field on old messages

v3.0.137
----------
 * Don't assume event fires still exist in process_fire_events
 * Add new Msg.attachments field to hold multiple attachments on an incoming message

v3.0.136
----------
 * Fix scheduled broadcast text display

v3.0.135
----------
 * Make 'only' keyword triggers ignore punctuation
 * Make check_campaigns_task lock on the event fires that it will queue
 * Break up flow event fires into sub-batches of 500
 * Ignore and ack incoming messages from Android relayer that have no number

v3.0.134
----------
 * Add match_type option to triggers so users can create triggers which only match when message only contains keyword
 * Allow Africa's talking to retry sending message
 * Allow search on the triggers pages
 * Clear results for analytics when user removes a flow run

v3.0.133
----------
 * Make Msg.get_sync_commands more efficent
 * Fix open range airtime transfers
 * Fix multiple Android channels sync
 * Fix parsing of macrokiosk channel time format
 * Ensure that our select2 boxes show "Add new" option even if there is a partial match with an existing item
 * Switch to new translatable fields and remove old Broadcast fields
 * Add Firebase Cloud messaging support for Android channels

v3.0.132
----------
 * Migration to populate new translatable fields on old broadcasts. This migration is slow on a large database so it's
   recommended that large deployments fake it and run it manually.

v3.0.128
----------
 * Add new translatable fields to Broadcast and ensure they're populated for new stuff

v3.0.127
----------
 * Fix autocomplete for items containing digits or other items
 * Make autocomplete dropdown disappear when user clicks in input box
 * Replace usages of "SMS" with "message" in editor
 * Allow same subflow to be called without pause in between

v3.0.126
----------
 * Fix exporting messages by a label folder
 * Improve performance of org export page for large orgs
 * Make it easier to enable/disable debug toolbar
 * Increase channel logging for requests and responses
 * Change contact api v1 to insert nonexistent fields
 * Graceful termination of USSD sessions

v3.0.125
----------
 * Don't show deleted flows on list page
 * Convert timestamps sent by MacroKiosk from local Kuala Lumpur time

v3.0.124
----------
 * Move initial IVR expiration check to status update on the call
 * Hide request time in channel log if unset
 * Check the existance of broadcast recipients before adding
 * Voice flows import should never allow expirations longer than 15 mins
 * Fix parse location to correctly use the tokenizized text if the location was matched for the entire text
 * Use updates instead of full Channel saves() on realyer syncs, only update when there are changes

v3.0.123
----------
 * Use flow starts for triggers that operate on groups
 * Handle throttling errors from Nexmo when using API to add new numbers
 * Convert campaign event messages to HSTORE fields

v3.0.121
----------
 * Add MACROKIOSK channel type
 * Show media for MMS in simulator

v3.0.120
----------
 * Fix send all bug where we append list of messages to another list of messages
 * Flows endpooint should allow filtering by modified_on

v3.0.119
----------
 * More vertical form styling tweaks

v3.0.118
----------
 * Add flow link on subflow rulesets in flows

v3.0.117
----------
 * Fix styling on campaign event modal

v3.0.116
----------
 * Update to latest Raven
 * Make default form vertical, remove horizontal to vertical css overrides
 * Add flow run search and deletion
 * Hangup calls on channels release

v3.0.115
----------
 * Allow message exports by label, system label or all messages
 * Fix for double stacked subflows with immediate exits

v3.0.112
----------
 * Archiving a flow should interrupt all the current runs

v3.0.111
----------
 * Display webhook results on contact history
 * Clean up template tags used on contact history
 * Allow broadcasts to be sent to all urns belonging to the specified contacts

v3.0.109
----------
 * Data migration to populate broadcast send_all field

v3.0.108
----------
 * Add webhook events trim task with configurable retain times for success and error logs

v3.0.107
----------
 * Add send_all broadcast field

v3.0.106
----------
 * Remove non_atomic_gets and display message at /api/v1/ to explain API v1 has been replaced
 * Add squashable model for label counts
 * Split system label functionality into SystemLabel and SystemLabelCount

v3.0.105
----------
 * Link subflow starts in actions
 * Allow wait to wait in flows with warning

v3.0.104
----------
 * Add new has email test, contains phrase test and contains only phrase test

v3.0.103
----------
 * Migration to populate FlowNodeCount shouldn't include test contacts

v3.0.102
----------
 * Add migration to populate FlowNodeCount

v3.0.101
----------
 * Migration to clear no-longer-used flow stats redis keys
 * Replace remaining cache-based flow stats code with trigger based FlowNodeCount

v3.0.100
----------
 * Fix intermittently failing Twilio test
 * make sure calls have expiration on initiation
 * Update to latest smartmin
 * Add redirection for v1 endpoints
 * Fix webhook docs
 * Fix MsgCreateSerializer not using specified channel
 * Test coverage
 * Fix test coverage issues caused by removing API v1 tests
 * Ensure surveyor users still have access to the API v2 endpoint thats they need
 * Remove djangorestframework-xml
 * Restrict API v1 access to surveyor users
 * Block all API v2 writes for suspended orgs
 * Remove all parts of API v1 not used by Surveyor

v3.0.99
----------
 * Prioritize msg handling over timeotus and event fires
 * Remove hamlcompress command as deployments should use regular compress these days
 * Fix not correctly refreshing dynamic groups when a URN is removed
 * Allow searching for contacts *with any* value for a given field

v3.0.98
----------
 * Fix sidebar nav LESS so that level2 lists don't have fixed height and separate scrolling
 * Unstop a contact when we get an explicit user interaction such as follow

v3.0.96
----------
 * Fix possible race condition between receiving and handling messages
 * Do away with scheme for USSD, will always be TEL
 * Make sure events are handled properly for USSD
 * Do not specify to & from when using reply_to
 * Update JunebugForm for editing Junebug Channel + config fields

v3.0.95
----------
 * Log request time on channel log success

v3.0.94
----------
 * Fix test, fix template tags

v3.0.93
----------
 * Change request times to be in ms instead of seconds

v3.0.92
----------
 * Block on handling incoming msgs so we dont process them forever away
 * Include Viber channels in new conversation trigger form channel choices

v3.0.90
----------
 * Don't use cache+calculations for flow segment counts - these are pre-calculated in FlowPathCount
 * Do not include active contacts in flows unless user overrides it
 * Clean up middleware imports and add tests
 * Feedback to user when simulating a USSD channel without a USSD channel connected

v3.0.89
----------
 * Expand base64 charset, fix decode validity heuristic

v3.0.88
----------
 * Deal with Twilio arbitrarily sending messages as base64
 * Allow configuration of max text size via settings

v3.0.87
----------
 * Set higher priority when sending responses through Kannel

v3.0.86
----------
 * Do not add stopped contacts to groups when importing
 * Fix an entire flow start batch failing if one run throws an exception
 * Limit images file size to be less than 500kB
 * Send Facebook message attachments in a different request as the text message
 * Include skuid for open range tranfertto accounts

v3.0.85
----------
 * Fix exception when handling Viber msg with no text
 * Migration to remove no longer used ContactGroup.count
 * Fix search queries like 'foo bar' where there are more than one condition on name/URN
 * Add indexes for Contact.name and ContactURN.path
 * Replace current omnibox search function with faster and simpler top-25-of-each-type approach

v3.0.84
----------
 * Fix Line, FCM icons, add Junebug icon

v3.0.83
----------
 * Render missing field and URN values as "--" rather than "None" on Contact list page

v3.0.82
----------
 * Add ROLE_USSD
 * Add Junebug USSD Channel
 * Fix Vumi USSD to use USSD Role

v3.0.81
----------
 * Archive triggers that do not have a contact to send to
 * Disable sending of messages for blocked and stopped contacts

v3.0.80
----------
 * Add support for outbound media on reply messages for Twilio MMS (US, CA), Telegram, and Facebook
 * Do not throw when viber sends us message missing the media
 * Optimizations around Contact searching
 * Send flow UUID with webhook flow events

v3.0.78
----------
 * Allow configuration of max message length to split on for External channels

v3.0.77
----------
 * Use brand key for evaluation instead of host when determining brand
 * Add red rabbit type (hidden since MT only)
 * Fix flow results exports for broadcast only flows

v3.0.76
----------
 * Log Nexmo media responses without including entire body

v3.0.75
----------
 * Dont encode to utf8 for XML and JSON since they expect unicode
 * Optimize contact searching when used to determine single contact's membership
 * Use flow system user when migrating flows, avoid list page reorder after migrations

v3.0.74
----------
 * reduce number of lookup to DB

v3.0.73
----------
 * Add test case for search URL against empty field value
 * Fix sending vumi messages initiated from RapidPro without response to

v3.0.72
----------
 * Improvements to external channels to allow configuration against JSON and XML endpoints
 * Exclude test contacts from flow results
 * Update to latest smartmin to fix empty string searching

v3.0.70
----------
 * Allow USSD flows to start someone else in a flow
 * Include reply to external_id for Vumi channel

v3.0.69
----------
 * Add ID column to result exports for anon orgs
 * Deactivate runs when releasing flows
 * Fix urn display for call log
 * Increased send and receive channel logging for Nexmo, Twilio, Twitter and Telegram 
 * Allow payments through Bitcoins
 * Include TransferTo account currency when asking phone info to TransferTo
 * Don't create inbound messages for gather timeouts, letting calls expire
 * Don't show channel log for inactive channels on contact history
 * Upgrade to latest smartmin which changes created_on/modified_on fields on SmartModels to be overridable
 * Uniform call and message logs

v3.0.64
----------
 * Add ID column to anonymous org contact exports, also add @contact.id field in message context
 * Fix counts for channel log elements
 * Only have one link on channel page for sending log
 * Attempt to determine file types for msg attachments using libmagic
 * Deactivate runs on hangups, Keep ivr runs open on exit
 * Add log for nexmo media download
 * Add new perf_test command to run performance tests on database generated with make_test_db

v3.0.62
----------
 * Fix preferred channels for non-msg channels

v3.0.61
----------
 * Make migrations to populate new export task fields non-atomic
 * Add indexes for admin boundaries and aliases
 * Nexmo: make sure calls are ended on hangup, log hangups and media
 * Fix inbound calls on Nexmo to use conversation_uuid
 * Style tweaks for zapier widget
 * Use shorter timeout for IVR
 * Issue hangups on expiration during IVR runs
 * Catch all exceptions and log them when initiating call
 * Fix update status for Nexmo calls

v3.0.48
----------
 * Add channel session log page
 * Use brand variable for zaps to show
 * Additional logging for nexmo
 * Increase non-overlap on timeout queueing, never double queue single timeout
 * Fix broken timeout handling when there is a race
 * Make field_keys a required parameter
 * Speed up the contact import by handling contact update at once after all the fields are set

v3.0.47
----------
 * Add channel log for Nexmo call initiation
 * Fix import-geojson management command

v3.0.46
----------
 * Fix Contact.search so it doesn't evaluate the base_query
 * Enable searching in groups and blocked/stopped contacts

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
