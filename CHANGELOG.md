v3.0.334
----------
 * Add response to external ID to courier serialized msg if we have response to
 * More Py3 migration work
 * Remove broadcasting to groups from Send Message dialog

v3.0.332
----------
 * Do not delete RuleSets only disconnect them from flows

v3.0.331
----------
 * Fix scoping for sim show/hide

v3.0.330
----------
 * Allow toggling of new engine on demand with /v2 command in simulator

v3.0.329
----------
 * Fix negative cache ttl for topups

v3.0.328
----------
 * Remove Vumi Type
 * Remove custom autoscaler for Celery
 * Implement Plivo without Plivo library

v3.0.325
----------
 * Build dynamic groups in background thread
 * Dynamic Channel changes, use uuids in URLs, allow custom views
 * Allow WhatsApp channels to refresh contacts manually
 * Allow brands to specifiy includes for the document head
 * Fix external claim page, rename auth_urn for courier
 * Change VB channel type to be a dynamic channel
 * Remove unused templates

v3.0.324
----------
 * Add ability to run select flows against a flowserver instance

v3.0.323
----------
 * Move JioChat access creation to channel task
 * Use 'list()' on python3 dict iterators
 * Use analytics-python===1.2.9, python3 compatible
 * Fix using PlayAction in simulator and add tests
 * Fix HasEmailTest to strip surrounding punctuation
 * ContainsPhraseTest shouldn't blow up if test string is empty
 * Use 'six' library for urlparse, urlencode

v3.0.322
----------
 * Unfreeze phonenumbers library so we always use latest
 * Remove old Viber VI channel type
 * Add config template for LN channel type
 * Move configuration blurbs to channel types
 * Move to use new custom model JSONAsTextField where appropriate

v3.0.321
----------
 * Fix quick-reply button in flow editor

v3.0.320
----------
 * Fix webhook rule as first step in run interpreting msg wrong
 * Change mailto URN importing to use header 'mailto' and make 'email' always a field. Rename 'mailto' fields to 'email'.

v3.0.319
----------
 * Add ArabiaCell channel type
 * Tweaks to Mtarget channel type
 * Pathfix for highcharts

v3.0.318
----------
 * Add input to webhook payload

v3.0.317
----------
 * Remove support for legacy webhook payload format
 * Fix org-choose redirects for brands

v3.0.316
----------
 * Remove stop endpoint for MT

v3.0.315
----------
 * Inactive flows should not be listed on the API endpoint
 * Add Mtarget channel type

v3.0.314
----------
 * Add run dict to default webhook payload

v3.0.313
----------
 * have URNs resolve to dicts instead of just the display
 * order transfer credit options by name
 * show dashboard link even if org is chosen

v3.0.312
----------
 * include contact URN in webhook payload

v3.0.311
----------
 * Allow exporting results of archived flows
 * Update Twitter Activity channels to work with latest beta changes
 * Increase maximum attachment URL length to 2048
 * Tweak contact searching so that set/not-set conditions check the type specific column
 * Migration to delete value decimal/datetime instances where string value is "None"
 * Don't normalize nulls in @extra as "None"
 * Clear timeouts for msgs which dont have credits assigned to them
 * Simpler contact get_or_create method to lookup a contact by urn and channel
 * Prevent updating name for existing contact when we receive a message
 * Remove fuzzy matching for ContainsTest

v3.0.310
----------
 * Reimplement clickatell as a Courier only channel against new API

v3.0.309
----------
 * Use database trigger for inserting new recent run records
 * Handle stop contact channel events
 * Remove no longer used FlowPathRecentRun model

v3.0.308
----------
'# Enter any comments for inclusion in the CHANGELOG on this revision below, you can use markdown
 * Update date for webhook change on api docs
 * Don't use flow steps for calculating test contact activity

v3.0.307
----------
 * Stop using FlowPathRecentMessage

v3.0.306
----------
 * Migration to convert recent messages to recent runs

v3.0.305
----------
 * Add new model for tracking recent runs
 * Add dynamic group optimization for new contacts

v3.0.304
----------
 * Drop index on FlowStep.step_uuid as it's no longer needed

v3.0.303
----------
 * Still queue messages for sending when interrupted by a child

v3.0.302
----------
 * Use FlowRun.current_node_uuid for sending to contacts at a given flow node

v3.0.301
----------
 * Tweak process_message_task to not blow up if message doesn't exist
 * Use FlowRun.message_ids for flow result exports

v3.0.300
----------
 * Use config secret instead of secret field on Channel
 * Add tests for datetime contact API field update

v3.0.299
----------
 * Fix deleting resthooks
 * Fix quick replies UI on Firefox

v3.0.298
----------
 * Process contact queue until there's a pending message or empty
 * Make date parsing much stricter
 * Migration to fix run results which were numeric but parsed as dates
 * Use transaction when creating contact URN
 * Add support for v2 webhooks

v3.0.294
----------
 * Fix run.path trigger to not blow up deleting old steps that don't have exit_uuids
 * Define MACHINE_HOSTNAME for librato metrics

v3.0.293
----------
 * Fix handle_ruleset so we don't continue the run if a child has exited us
 * Migration to backfill FlowRun.message_ids and .current_node_uuid (recommend faking and running manually)

v3.0.292
----------
 * Add support for 'direct' db connection
 * Stop updating count and triggered on on triggers
 * Add FlowRun.current_node_uuid and message_ids
 * Catch IntegrityError and lookup again when creating contact URN
 * Make sure we dont allow group chats in whatsapp

v3.0.291
----------
 * Ignore TMS callbacks

v3.0.289
----------
 * Stop writing values in flows to values_value

v3.0.287
----------
 * Performance improvements and simplications to flow result exports
 * Add some extra options to webhook_stats
 * Migration to convert old recent message records

v3.0.286
----------
 * Remove incomplete path counts

v3.0.285
----------
 * Migrate languages on campaign events
 * Rework flow path count trigger to use exit_uuid and not record incomplete segments

v3.0.282
----------
 * Don't import contacts with unknown iso639-3 code
 * Make angular bits less goofy for quick replies and webhooks
 * Add is_active index on flowrun
 * Don't disassociate channels from orgs when they're released
 * Include language column in Contact export

v3.0.281
----------
 * Set tps for nexmo and whatsapp
 * Dont overwrite name when receiving a message from a contact that already exists
 * Flow start performance improvements

v3.0.280
----------
 * Parse ISO dates followed by a period
 * Optimize batch flow starts

v3.0.279
----------
 * Update Nexmo channels to use new Courier URLs
 * Store path on AdminBoundary for faster lookups
 * Serialize metata for courier tasks (quick replies support)
 * Add default manager to AdminBoundary which doesn't include geometry

v3.0.278
----------
 * Fixes to the ISO639-3 migration
 * Add support for quick replies

v3.0.277
----------
 * Add flow migration for base_language in flow definitions

v3.0.276
----------
 * back down to generic override if not found with specific code
 * Add esp-spa as exception

v3.0.275
----------
 * Fix language migrations

v3.0.274
----------
 * Fix serialization of 0 decimal values in API
 * Add initial version of WhatsApp channel (simple messaging only)
 * Migrate to iso639-3 language codes (from iso639-2)
 * Remove indexes on Msg, FlowRun and FlowStep which we don't use
 * Remove fields no longer used on org model

v3.0.273
----------
 * Don't blow up when a flow result doesn't have input

v3.0.272
----------
 * Fix parsing ISO dates with negative offsets

v3.0.271
----------
 * Serialize contact field values with org timezone

v3.0.270
----------
 * Load results and path from new JSON fields instead of step/value objects on API runs endpoint

v3.0.269
----------
 * Fix campaign export issue
 * Disable legacy analytics page
 * Change date constants and contact fields to use full/canonical format in expressions context

v3.0.265
----------
 * Fix not updating versions on import flows
 * Require FlowRun saves to use update_fields
 * Rework get_results to use FlowRun.results
 * Don't allow users to save dynamic groups with 'id' or 'name' attributes
 * Add flow version 11.0, create migration to update references to contact fields and flow fields

v3.0.264
----------
 * Show summary for non-waits on flow results
 * Reduce number of queries during flow handling

v3.0.263
----------
 * Start campaigns in separate task
 * Enable flow results graphs on flow result page
 * Fix run table json parsing
 * SuperAutoScaler!

v3.0.262
----------
 * Use string comparison to optimize temba_update_flowcategorycount
 * Allow path counts to be read by node or exit
 * SuperAutoscaler
 * Fix inbox views so we don't look up channel logs for views that don't have them
 * Add management command for analyzing webhook calls
 * Change recent message fetching to work with either node UUID or exit UUID

v3.0.261
----------
 * Migrate revisions forward with rev version
 * Limit scope of squashing so we can recover from giant unsquashed numbers

v3.0.260
----------
 * Make tests go through migration
 * Set version number of system created flows
 * Block saving old versions over new versions
 * Perform apply_topups as a task, tweak org update form
 * Updates to credit caches to consider expiration
 * Tweak credit expiration email

v3.0.259
----------
 * Improve performance and restartability of run.path backfill migration
 * Update to latest smartmin
 * Use run.results for run results page

v3.0.258
----------
 * Set brand domain on channel creations, use for callbacks

v3.0.257
----------
 * Migration to populate run paths (timeconsuming, may want to fake aand run manually)
 * Ensure actions have UUIDs in single message and join-group flows
 * Flow migration command shouldn't blow up if a single flow fails

v3.0.255
----------
 * Fix Twilio to redirect to twilio claim page after connecting Twilio
 * Add FlowRun.path and start populating it for new flow steps
 * Removes no longer used Msg.has_template_error field

v3.0.254
----------
 * Use get_host() when calculating signature for voice callbacks

v3.0.253
----------
 * use get_host() when validating IVR requests

v3.0.252
----------
 * Better Twilio channel claiming

v3.0.250
----------
 * Tweaks to recommended channels display

v3.0.246
----------
 * Update smartmin to version 1.11.4
 * Dynamic channels: Chikka, Twilio, Twilio Messaging Service and TwiML Rest API

v3.0.245
----------
 * Tweaks to the great FlowRun results migration for better logging and for parallel migrations
 * Fixes us showing inactive orgs in nav bar and choose page
 * Ignore requests missing text for incoming message from Infobip

v3.0.244
----------
 * Add exit_uuid to all flow action_sets (needed for goflow migrations)

v3.0.243
----------
 * Add index to FlowPathRecentMessage
 * Flows API endpoint should filter out campaign message flow type
 * Add archived field to campaings API endpoint
 * Fix to correctly substitute context brand variable in dynamic channel blurb

v3.0.242
----------
 * Data migration to populate results on FlowRun (timeconsuming, may want to fake and run manually)

v3.0.239
----------
 * Migration to increase size of category count

v3.0.238
----------
 * Increase character limits on category counts

v3.0.237
----------
 * Fix Nexmo channel link
 * Add results field to FlowRun and start populating
 * Add FlowCategoryCount model for aggregating flow results
 * Remove duplicate USSD channels section

v3.0.234
----------
 * Remove single message flows when events are deleted

v3.0.233
----------
 * Remove field dependencies on flow release, cleanup migration
 * Update to latest Django 1.11.6

v3.0.232
----------
 * Mage handler shouldn't be accessible using example token in settings_common
 * Make Msg.has_template_error nullable and stop using it

v3.0.231
----------
 * Add claim page for dmark for more prettiness
 * Add management command to migrate flows forward
 * Add flow migration for partially localized single message flows
 * Recalculate topups more often
 * Add dmark channel (only can send and receive through courier)
 * Merge pull request #1522 from nyaruka/headers
 * Replace TEMBA_HEADERS with http_headers()
 * Improve mock server used by tests so it can mock specifc url with specific responses
 * Add method to get active channels of a particular channel type category
 * Replace remaining occurrences of assertEquals
 * Fix the way to check USSD support
 * Dynamic channels: Vumi and Vumi USSD

v3.0.230
----------
 * Deal with malformed group format as part of group updates
 * Allow installs to configure how many fields they want to keep in @extra
 * Fix Nexmo icon
 * Add logs for incoming requests for InfoBip
 * Do both Python 2 and 3 linting in a single build job

v3.0.229
----------
 * Do not set external ID for InfoBip we have send them our ID
 * Fix channel address comparison to be insensitive to +
 * Use status groupId to check from the InfoBip response to know if the request was erroneous

v3.0.228
----------
 * Add id to reserved field list

v3.0.227
----------
 * Update Infobip channel type to use the latest JSON API
 * Migrate flows forward to have dependencies

v3.0.226
----------
 * Fix issue with dates in the contact field extractor
 * Allow org admin to remove invites

v3.0.225
----------
 * Optimize how we check for unsent messages on channels
 * Ensure all actions have a UUID in new flow spec version 10.1
 * Fixes viber URN validation: can be up to 24 chars
 * Dynamic channels: Zenvia, YO
 * Add support for minor flow migrations

v3.0.224
----------
 * Remove duplicate excellent includes (only keep compressed version)

v3.0.222
----------
 * Only show errors in UI when org level limits of groups etc are exceeded 
 * Improve error messages when org reaches limit of groups etc

v3.0.221
----------
 * Add indexes for retying webhook events

v3.0.220
----------
 * Remove no longer used Msg.priority (requires latest Mage)

v3.0.219
----------
 * Create channel event only for active channels
 * Limit SMS Central channel type to the Kathmandu timezone
 * Create fields from expressions on import
 * Flow dependencies for fields, groups, and flows
 * Dynamic channels: Start
 * Dynamic channels: SMS Central

v3.0.218
----------
 * Delete simulation messages in batch of 25 to use the response_to index
 * Fix Kannel channel type icon
 * @step.contact and @contact should both be the run contact
 * Migration to set value_type on all RuleSets

v3.0.217
----------
 * Add page titles for common pages
 * New index for contact history
 * Exit flows in batches so we dont have to grab all runs at once
 * Check we can create a new groups before importing contact and show the error message to the user
 * Fixes value type guessing on rulesets (we had zero typed as dates)
 * Update po files
 * Dynamic channels: Shaqodoon

v3.0.216
----------
 * Should filter user groups by org before limiting to 250
 * Fixes for slow contact history
 * Allow updating existing fields via API without checking the count
 * Update TWIML IVR protocol check
 * Add update form fields in dynamic channel types
 * Abstract out the channel update view form classes
 * Add ivr_protocol field on channel type
 * Mock constants to not create a lot of objects in test DB
 * Limit the contact fields max per org to 200 to below the max form post fields allowed
 * Limit number of contact groups creation on org to 250
 * Limit number of contact fields creation on org to 250
 * Dynamic channels: Red Rabbit, Plivo Nexmo

v3.0.212
----------
 * Make Msg.priority nullable so courier doesn't have to write to it
 * Calculate TPS cost for messages and add them to courier queues
 * Fix truncate cases in SQL triggers
 * Fix migration to recreate trigger on msgs table
 * Dynamic channels: Mblox

v3.0.211
----------
 * Properly create event fires for campaign events updated through api
 * Strip matched string in not empty test
 * Dynamic channels: Macrokiosk

v3.0.210
----------
 * Make message priority be based on responded state of flow runs
 * Support templatized urls in media
 * Add UI for URL Attachments
 * Prevent creation of groups and labels at flow run time
 * Dynamic channels: M3Tech, Kannel, Junebug and Junebug USSD

v3.0.209
----------
 * Add a way to specify the prefixes short codes should be matching
 * Include both high_priority and priority in courier JSON
 * Fix TwiML migration
 * Fix JSON response when searching Plivo numbers

v3.0.208
----------
 * Msg.bulk_priority -> Msg.high_priority
 * Change for currencies for numeric rule
 * Dynamic channels for Jasmin, Infobip, and Hub9

v3.0.207
----------
 * Fix Twiml config JSON keys
 * Unarchiving a campaign should unarchive all its flows

v3.0.206
----------
 * Fix broken Twilio Messaging Service status callback URL
 * Only update dynamic groups from set_field if value has changed
 * Optimize how we lookup contacts for some API endpoints
 * More dynamic channels

v3.0.205
----------
 * add way to show recommended channel on claim page for dynamic channels
 * change Org.get_recommended_channel to return the channel type instead of a random string

v3.0.204
----------
 * separate create and drop index operations in migration

v3.0.203
----------
 * create new compound index on channel id and external id, remove old external id index
 * consistent header for contact uuid in exports and imports
 * unstop contacts in handle message for new messages
 * populate @extra even on webhook failures
 * fix flow simulator with chatbase connected
 * use ContactQL for name of contact querying grammar
 * dynamic channels: Clickatell
 * fix contact searching where text includes + or / chars
 * replace Ply with ANTLR for contact searching (WIP)

v3.0.201
----------
 * Make clean string method replace non characteres correctly

v3.0.200
----------
 * Support Telegram /start command to trigger new conversation trigger

v3.0.199
----------
 * Use correct Twilio callback URL, status is for voice, keep as handler

v3.0.198
----------
 * Add /c/kn/uuid-uuid-uuid/receive style endpoints for all channel types
 * Delete webhook events in batches
 * Dynamic channels: Blackmyna

v3.0.197
----------
 * update triggers so that updates in migration work

v3.0.196
----------
 * make sure new uuids are honored in in_group tests
 * removes giant join through run/flow to figure out flow steps during export
 * create contacts from start flow action with ambiguous country
 * add tasks for handling of channel events, update handlers to use ChannelEvent.handle
 * add org level dashboard for multi-org organizations

v3.0.195
----------
 * Tweaks to allow message handling straight from courier

v3.0.193
----------
 * Add flow session model and start creating instances for IVR and USSD channel sessions

v3.0.192
----------
 * Allow empty contact names for surveyor submissions but make them null
 * Honor admin org brand in get_user_orgs
 * Fix external channel bulk sender URL
 * Send broadcast in the same task as it is created in and JS utility method to format number
 * Try the variable as a contact uuid and use its contact when building recipients
 * Fix org lookup, use the same code path for sending a broadcast
 * Fix broadcast to flow node to consider all current contacts on the the step

v3.0.191
----------
 * Update test_db to generate deterministic UUIDs which are also valid UUID4

v3.0.190
----------
 * Turn down default courier TPS to 10/s

v3.0.189
----------
 * Make sure msg time never wraps in the inbox

v3.0.188
----------
 * Use a real but mockable HTTP server to test flows that hit external URLs instead of mocking the requests
 * Add infobip as dynamic channel type and Update it to use the latest Infobip API
 * Add support for Courier message sending

v3.0.183
----------
 * Use twitter icon for twitter id urns

v3.0.182
----------
 * Tweak test_start_flow_action to test parent run states only after child runs have completed
 * Stop contacts when they have only an invalid twitter screen name
 * Change to max USSD session length

v3.0.181
----------
 * Ignore case when looking up twitter screen names

v3.0.180
----------
 * Switch to using twitterid scheme for Twitter messages
 * Should be shipped before Mage v0.1.84

v3.0.179
----------
 * Allow editing of start conversation triggers

v3.0.178
----------
 * Remove urn field, urn compound index, remove last uses of urn field

v3.0.177
----------
 * remove all uses of urn (except when writing)
 * create display index, backfill identity
 * Allow users to specify extra URNs columns to include on the flow results export

v3.0.176
----------
 * Add display and identity fields to ContactURN
 * Add schemes field to allow channels to support more than one scheme

v3.0.175
----------
 * Fix incorrect lambda use so message sending works

v3.0.174
----------
 * Make ContactField.uuid unique and non-null

v3.0.173
----------
 * Add migration to populate ContactField.uuid

v3.0.172
----------
 * Only try to delete Twilio app when channel config contains 'application_sid'
 * Surveyor submissions should try rematching the rules if the same ruleset got updated by the user and old rules were removed
 * Add uuid field to ContactField
 * Convert more channel types to dynamic types 

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
