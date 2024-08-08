v9.3.16 (2024-08-08)
-------------------------
 * Stop generating prometheus API tokens
 * Drop Ticket.body

v9.3.15 (2024-08-08)
-------------------------
 * Add Org.prometheus_token and backill from API tokens

v9.3.14 (2024-08-08)
-------------------------
 * Update tests to not set ticket body
 * Add data migration to move body to ticket on open ticket event

v9.3.13 (2024-08-08)
-------------------------
 * Show notes on ticket open events in contact history
 * Remove body from ticket endpoint documentation
 * Update floweditor which now also refers to ticket body as note
 * Update open ticket modal to use note instead of body
 * Add cutoff date for using viewer role

v9.3.12 (2024-08-07)
-------------------------
 * Don't create surveyor user in mailroom test db
 * Add warning to manage accounts page if org has viewers
 * Remove viewers as an org feature, only allow existing viewer users to remain as viewers
 * Update to latest Django

v9.3.11 (2024-08-07)
-------------------------
 * Remove Org.surveyor_password and always disable creating surveyor flows
 * Remove non-modal response support from export translation view
 * Remove surveyor user role and test user

v9.3.10 (2024-08-07)
-------------------------
 * Remove surveyor users from workspaces

v9.3.9 (2024-08-07)
-------------------------
 * Fix incidents templates name
 * Let Ticket.body be null and make note length match contact note length

v9.3.8 (2024-08-06)
-------------------------
 * Show tabs on tickets when contact is set

v9.3.7 (2024-08-06)
-------------------------
 * Add contact notes ui

v9.3.6 (2024-08-06)
-------------------------
 * Adjust the grant view for new UI
 * Fix Android claim page
 * Add incident for Android client app version out of date
 * Tweak fail_old_messages to only fail Android messages and add an index

v9.3.5 (2024-07-31)
-------------------------
 * Support FCM changes
 * Require E164 phone numbers for contacts created from UI

v9.3.4 (2024-07-30)
-------------------------
 * Add contact notes and expose over contacts API endpoint

v9.3.3 (2024-07-29)
-------------------------
 * Clamp messages on message views to one line
 * Adjust max length for AT API key
 * Make 'New Field' a button

v9.3.2 (2024-07-29)
-------------------------
 * Allow deleting of empty ticket topics
 * Add support for buttons in side menu and use where appropriate

v9.3.0 (2024-07-25)
-------------------------
 * Add User.get_by_email to ensure consistent behaviour where we look up a user by their email
 * Omnibox fixes and cleanup

v9.2.5 (2024-07-24)
-------------------------
 * Ensure that emails are consistently treated as case insensitive

v9.2.4 (2024-07-23)
-------------------------
 * Simplify FCM config setting names

v9.2.3 (2024-07-23)
-------------------------
 * More updates to WhatsApp claiming

v9.2.2 (2024-07-23)
-------------------------
 * Fix WhatsApp embedded signup

v9.2.1 (2024-07-18)
-------------------------
 * Catch errors from xlrd reading import rows and return errors with row numbers
 * Update xlrd
 * Honor meta key keyboard press inside contact chat

v9.2.0 (2024-07-17)
-------------------------
 * Simplify permissions in flows app
 * Tweak menu items for msg views and flow results

v9.1.198 (2024-07-17)
-------------------------
 * Allow template image variables to be text with expressions

v9.1.196 (2024-07-16)
-------------------------
 * Add __repr__ to more models and tweak existing ones for consistency
 * Fix rendering of flow starts for deleted flows
 * Add data migration to trim old broadcasts to nodes that resulted in very large contact lists

v9.1.195 (2024-07-16)
-------------------------
 * Remove special error handling for broadcast to node that resolves to no recipients
 * Fix setting a template on a new broadcast
 * Fix query broadcast creation and update
 * Add rendering of exclusions on broadcasts
 * Fix not showing query on broadcast recipients list and add node_uuid

v9.1.194 (2024-07-15)
-------------------------
 * Add Broadcast.node_uuid field
 * Remove old code for getting message created_by from broadcasts
 * Make some exception clauses more specific

v9.1.193 (2024-07-15)
-------------------------
 * Replace TemplateTranslation.STATUS_UNSUPPORTED completely

v9.1.192 (2024-07-15)
-------------------------
 * Add new template statuses and stop using fake "unsupported" status

v9.1.191 (2024-07-15)
-------------------------
 * Fix deactivating a legacy WhatsApp channel
 * Update format of templates on API endpoint
 * Show template translation problems as errors on template read page

v9.1.190 (2024-07-12)
-------------------------
 * Fix padding for broadcast schedule update

v9.1.189 (2024-07-12)
-------------------------
 * Fix mailroom_db
 * Data migration to populate TemplateTranslation.is_supported and is_compatible

v9.1.188 (2024-07-12)
-------------------------
 * Add new boolean fields to TemplateTranslation model to determine whether it's usable

v9.1.187 (2024-07-12)
-------------------------
 * Add templates to broadcasts

v9.1.186 (2024-07-11)
-------------------------
 * Fix handling of POSTs to API docs
 * Exclude empty templates from list, and show base translation apart on read page
 * Ensure we choose a new base for a template whenever an existing base translation is deleted

v9.1.185 (2024-07-11)
-------------------------
 * Update deps
 * Replace telegram library by requests use
 * Fix dashboard menu link permission
 * Expose Template.base_translation on API endpoint

v9.1.184 (2024-07-11)
-------------------------
 * Use dropdowns for location fields

v9.1.183 (2024-07-11)
-------------------------
 * Use dropdowns for location fields

v9.1.182 (2024-07-10)
-------------------------
 * Locations API endpoint should allow searching on the path
 * Fix template syncing when channel gives us invalid template data

v9.1.181 (2024-07-10)
-------------------------
 * Add Template.base_translation
 * Fix dashboard workspace data
 * Allow creation of contacts with non-active statuses

v9.1.180 (2024-07-10)
-------------------------
 * Drop no longer used is_active field from TemplateTranslation
 * Tweak wording on template list page
 * Add db constraint to ensure contact status is valid

v9.1.179 (2024-07-10)
-------------------------
 * Keep FCM ID in channel config when soft deleting the channel
 * Stop using TemplateTranslation.is_active and make nullable

v9.1.178 (2024-07-09)
-------------------------
 * Allow broadcast creation with zero matches

v9.1.177 (2024-07-08)
-------------------------
 * Hard delete remaining soft-deleted template translations

v9.1.176 (2024-07-08)
-------------------------
 * Update Template to a TembaModel
 * Hard delete template translations that no longer exist on the channel side

v9.1.175 (2024-07-05)
-------------------------
 * Make send_when optional when updating broadcasts

v9.1.174 (2024-07-05)
-------------------------
 * Fix updating scheduled broadcasts
 * Remove old unused code for queueing broadcasts

v9.1.173 (2024-07-05)
-------------------------
 * Add Msg.is_android field
 * Add internal API endpoint for searching locations by level and name
 * Remove option to send now on broadcast update

v9.1.172 (2024-07-04)
-------------------------
 * Add templates to broadcasts (hidden for now)
 * Remove deprecated broadcast.template_state field on mailroom queue payload

v9.1.171 (2024-07-03)
-------------------------
 * Update payload for queueing a bradocast

v9.1.170 (2024-07-03)
-------------------------
 * Remove no longer needed task to sync stale Android relayers
 * Don't allow template localization
 * Update dependencies

v9.1.169 (2024-07-02)
-------------------------
 * Use python 3.11.x
 * Add Broadcast.template_variables
 * Add new template list and read pages and remove old channel specific ones
 * Fix globals list template

v9.1.168 (2024-06-28)
-------------------------
 * Don't sync classifiers in suspended orgs
 * Fix empty contact search with query present

v9.1.167 (2024-06-28)
-------------------------
 * Disallow empty recipient targeting
 * Fix external links within spa container

v9.1.166 (2024-06-27)
-------------------------
 * Tweak logging for failure during classifier syncing
 * Switch broadcast tests to use contact search

v9.1.165 (2024-06-27)
-------------------------
 * Rework remaining mailroom client methods
 * Add unique constraint on template translations

v9.1.164 (2024-06-27)
-------------------------
 * Add data migration to remove duplicate template translations

v9.1.163 (2024-06-27)
-------------------------
 * Change template translation syncing to enforce uniqueness over channel+locale

v9.1.162 (2024-06-27)
-------------------------
 * Make templatetranslation locale non-null
 * Add migration to release translations for released channels

v9.1.161 (2024-06-27)
-------------------------
 * Fix not releasing template translations when channel released

v9.1.160 (2024-06-27)
-------------------------
 * Fix creating scheduled broadcasts
 * Tweak menu on campaign read page
 * Update to latest smartmin

v9.1.159 (2024-06-26)
-------------------------
 * Simplify some button labels and make edit a button on contact read page
 * Don't show empty contact filter list
 * Rework more mailroom client methods to use models instead of primitives

v9.1.158 (2024-06-26)
-------------------------
 * Add day selection when doing flow start search
 * Tweak mailroom_db to run on different port

v9.1.157 (2024-06-25)
-------------------------
 * Reorg of mailroom client
 * Add Broadcast.exclusions

v9.1.156 (2024-06-24)
-------------------------
 * Change broadcast creation from UI to use mailroom

v9.1.155 (2024-06-24)
-------------------------
 * Fix WAC to addEventListener in OnSpload
 * Fix horizontal scrolling for contacts list
 * Add Broadcast.template

v9.1.154 (2024-06-21)
-------------------------
 * Fix z-index issue properly

v9.1.153 (2024-06-21)
-------------------------
 * Fix z-index issue with content menu and chat

v9.1.152 (2024-06-21)
-------------------------
 * Fix ticket switching bug

v9.1.151 (2024-06-21)
-------------------------
 * Update chat rendering

v9.1.148 (2024-06-20)
-------------------------
 * Fix Broadcast.create

v9.1.147 (2024-06-20)
-------------------------
 * Use mailroom to create broadcasts from API calls
 * Use mailroom to send broadcasts to flow nodes

v9.1.146 (2024-06-17)
-------------------------
 * Don't clip footer when ticket history grows
 * Fix migration to add uuid field to airtime transfers

v9.1.145 (2024-06-17)
-------------------------
 * Don't send forgot password email if one was sent in last 5 minutes
 * Delete failed login records on successful password reset
 * Make transer UUID unique field, use TembaUUIDMixin on model

v9.1.144 (2024-06-14)
-------------------------
 * Add pagination on channel templates page
 * Add settings config for Android clients FCM config
 * Remove pyfcm and use google auth library to send sync messages for FCM
 * Create our own password recovery view

v9.1.143 (2024-06-12)
-------------------------
 * Update smartmin
 * Delete recovery tokens when new ones are created or email changed
 * Populate airtime transfer uuids

v9.1.142 (2024-06-12)
-------------------------
 * Add AirtimeTransfer.external_id
 * Add data migration to cleanup template translations

v9.1.141 (2024-06-12)
-------------------------
 * Update to latest smartmin
 * Add uuid field to airtime transfer model

v9.1.140 (2024-06-12)
-------------------------
 * Really actually fix template attachments for real

v9.1.139 (2024-06-11)
-------------------------
 * Fix split issue for template editor

v9.1.138 (2024-06-10)
-------------------------
 * Template editor fix for empty content
 * Tweak component types to be header/*, body/* etc
 * Support Twilio media in templates

v9.1.137 (2024-06-10)
-------------------------
 * Support WhatsApp templates with header images
 * Remove no longer used URN related code
 * Generate email verification secret when account created, change when email changed

v9.1.136 (2024-06-07)
-------------------------
 * Add spa mixin to transfer logs views
 * Allow editing TWA messaging service SID
 * Lean on mailroom for URN validation during contact update
 * Some tidy up of the update contact form

v9.1.135 (2024-06-05)
-------------------------
 * Fix login error message styling 
 * Remove unused JS libs

v9.1.134 (2024-06-05)
-------------------------
 * Contact API endpoint should let mailroom decide if a URN is taken
 * Revert "Remove csrf token hidden element not under a form"

v9.1.133 (2024-06-05)
-------------------------
 * Fix API explorer POSTs
 * Make CSRF cookie age 2 weeks and remove non-form hidden CSRF hidden elements

v9.1.132 (2024-06-04)
-------------------------
 * Make sure the CSRF element is present for all page header blocks

v9.1.131 (2024-05-31)
-------------------------
 * Fix DT One submit buttons

v9.1.130 (2024-05-31)
-------------------------
 * Fix flow and msgs unlabel action
 * Remove no longer used params field on synched whatsapp type templates

v9.1.129 (2024-05-29)
-------------------------
 * Increase DATA_UPLOAD_MAX_NUMBER_FIELDS to 2500
 * Fix FB and IG claim getFBpages

v9.1.128 (2024-05-27)
-------------------------
 * Lean on mailroom for validation of phone numbers from android events / messages

v9.1.127 (2024-05-27)
-------------------------
 * Rework contact create view to let mailroom do URN validation

v9.1.126 (2024-05-24)
-------------------------
 * Mailroom client should use content-type header on responses to know whether to parse as JSON
 * Ensure anon users can access API docs

v9.1.125 (2024-05-23)
-------------------------
 * Add csrf on hidden element

v9.1.124 (2024-05-22)
-------------------------
 * Rework handling of errors from mailroom client
 * Update test db flows

v9.1.123 (2024-05-20)
-------------------------
 * Replace django messages rendering with toasts

v9.1.121 (2024-05-16)
-------------------------
 * Fix action to remove from group.  
 * Report bulk action errors to users with django messages

v9.1.120 (2024-05-16)
-------------------------
 * Remove old unused ES sorting code
 * Update to latest smartmin and disable auto success messages
 * Add data migration to fix system fields for existing orgs and start using is_proxy
 * Reduce reserved keys for fields to bare minimum

v9.1.119 (2024-05-16)
-------------------------
 * Add ContactField.is_proxy and reduce SYSTEM_FIELDS to the two proxy date fields
 * Don't use error level alerts for form errors

v9.1.118 (2024-05-15)
-------------------------
 * Remove unused args from MailroomClient.parse_query
 * Re-add search errors to contact list views

v9.1.117 (2024-05-15)
-------------------------
 * Add support for unknown_property_type search errors
 * Add support for twilio card type content templates
 * Add way to view webhook logs errors only

v9.1.116 (2024-05-14)
-------------------------
 * Fix issues with twilio templates sync

v9.1.115 (2024-05-10)
-------------------------
 * Fix Twilio template type slug and register its template type

v9.1.114 (2024-05-10)
-------------------------
 * Add message templates menu for TWA channels
 * Activate Twilio Whatsapp to sync templates with twilio type
 * Update to allow matching sender ID as valid phones

v9.1.113 (2024-05-09)
-------------------------
 * Fix gaps it contact history

v9.1.112 (2024-05-09)
-------------------------
 * Ignore android msg/event cmds with non numeric phones

v9.1.111 (2024-05-08)
-------------------------
 * Send phone instead of urn to mailroom android endpoints
 * Add Twilio content template type, and TWA fetch_templates

v9.1.110 (2024-05-08)
-------------------------
 * Remove messages block that duplicates alert-messages
 * Tweak DefinitionExport.name for consistency

v9.1.109 (2024-05-07)
-------------------------
 * Tweak export finished emails so they don't say Excel

v9.1.108 (2024-05-07)
-------------------------
 * Update temba-components to 0.86.1
 * Change flow definitions export to be async, use new export type

v9.1.107 (2024-05-07)
-------------------------
 * Fix variable name in http log read page
 * Fix claiming instagram

v9.1.106 (2024-05-06)
-------------------------
 * Fix globals API endpoint

v9.1.105 (2024-05-03)
-------------------------
 * Fix race condition on editor load

v9.1.104 (2024-05-03)
-------------------------
 * Fix template bug and loading error for editor

v9.1.103 (2024-05-02)
-------------------------
 * Fix contact field selection

v9.1.102 (2024-05-02)
-------------------------
 * Delete all sessions and runs in org deletion in batches
 * Tiny style change for loader wrapping on editor

v9.1.101 (2024-05-01)
-------------------------
 * Update editor and flow spec version

v9.1.100 (2024-04-29)
-------------------------
 * Tweak time limit for sessions to 89 days so things are always interrupted before archiver gets to them
 * Cleanup API endpoint docs

v9.1.99 (2024-04-26)
-------------------------
 * Remove elastic search
 * Add support for read msg status

v9.1.98 (2024-04-25)
-------------------------
 * Fix ticket status selection

v9.1.97 (2024-04-25)
-------------------------
 * Include url for org chooser

v9.1.96 (2024-04-25)
-------------------------
 * Remove jQuery

v9.1.95 (2024-04-25)
-------------------------
 * Change ordering of non-search based exports to be id to match search based
 * Use mailroom endpoint for search based contact exports
 * Remove cancel button from contact import page and remove duplicate styles
 * Tweak layout of user edit form
 * Email notification that account email has changed should include the new email address

v9.1.94 (2024-04-24)
-------------------------
 * Fix changing password so user isn't logged out
 * Fix user edit form allowing insecure passwords

v9.1.93 (2024-04-24)
-------------------------
 * Add notification types for when email or password is changed
 * Expire unaccepted invitations after 30 days
 * Move invitation form into modal

v9.1.92 (2024-04-23)
-------------------------
 * Remove start url for surveyors and instead do login redirect
 * Fix to disallow content type vs extension mismatching for media uploads
 * Fix to limit sending user verification email to 1 per 10 minutes
 * Remove warning for flows that don't specify Facebook topic

v9.1.91 (2024-04-18)
-------------------------
 * Fix select race
 * Fix header matching
 * Simplify URL for template list page

v9.1.90 (2024-04-16)
-------------------------
 * Fix race on initial load for select and tabs

v9.1.89 (2024-04-16)
-------------------------
 * Fix API docs scrolling
 * Fix mailroom_db data file
 * Simplify channel claim page styling and remove unused styles
 * Add Msg.templating

v9.1.88 (2024-04-15)
-------------------------
 * Drop FlowRun.submitted_by and cleanup superfulous constants
 * Make whatsapp template type an actual package
 * Simplify page titles so section isn't repeated in title

v9.1.87 (2024-04-12)
-------------------------
 * Add inline attachment style and wrapping on logs
 * Don't re-release released triggers

v9.1.86 (2024-04-12)
-------------------------
 * Prune unnecessary styles, move to heavier fonts

v9.1.85 (2024-04-12)
-------------------------
 * Drop support for Submitted By in results exports
 * Add constraint to limit Msg.DIRECTION to I or O
 * Add constraint to incoming messages have channel and URN

v9.1.83 (2024-04-11)
-------------------------
 * Add TemplateType and rework whatsapp to be a type
 * Remove special treatment for exports of surveyor flows
 * Add TemplateTranslation.variables

v9.1.82 (2024-04-10)
-------------------------
 * Unpublicize the channel events API endpoint
 * Drop unused Msg.queued_on field

v9.1.81 (2024-04-10)
-------------------------
 * Update temba-components

v9.1.80 (2024-04-10)
-------------------------
 * Assume js is pre-minified

v9.1.79 (2024-04-09)
-------------------------
 * Update flow editor

v9.1.78 (2024-04-09)
-------------------------
 * Use new components bundle

v9.1.77 (2024-04-09)
-------------------------
 * Deprecate Msg.queued_on as it isn't used and make Msg.modified_on non-null

v9.1.76 (2024-04-08)
-------------------------
 * Add data migration to backfill missing user settings
 * Add signal receiver to ensure new users always have settings

v9.1.75 (2024-04-04)
-------------------------
 * Add data migration to archive campaigns with deleted groups
 * Fix rendering of campaigns with deleted groups
 * Improve styling on template list page

v9.1.74 (2024-04-04)
-------------------------
 * Update temba-components
 * Use timedate formatting for last_seen_on / created_on on contact list pages
 * Remove unused BRAND properties
 * Cleanup displaying of channel name, address and type

v9.1.73 (2024-04-03)
-------------------------
 * Make Channel.name non-null and remove unused channel list view
 * Replace format_datetime and short_datetime tags with day or datetime filters

v9.1.72 (2024-04-03)
-------------------------
 * Update temba-components
 * Add data migration to backfill empty channel names
 * Ensure Android channels get a default name when registering

v9.1.71 (2024-04-03)
-------------------------
 * Ignore empty messages from Android relayers

v9.1.70 (2024-04-03)
-------------------------
 * Update flow editor
 * Remove unused option on assets endpoint to return environment

v9.1.69 (2024-04-02)
-------------------------
 * Remove no longer used template tag as_icon
 * Fix export blocking due to multiple users exporting at same time
 * Switch formax to expand vertically
 * Add ChannelEvent.status field and prevent creating channel events of unknown types from Android syncs

v9.1.68 (2024-04-02)
-------------------------
 * Use mailroom endpoints to create messages and events during Android syncing
 * Drop support for returning template components as dict

v9.1.67 (2024-04-01)
-------------------------
 * Update template editor to work with comps as list
 * Add task to trim old channel events

v9.1.66 (2024-03-28)
-------------------------
 * Update format of tasks queued to mailroom

v9.1.65 (2024-03-28)
-------------------------
 * Update to django 5.0 and DRF 3.15.1

v9.1.64 (2024-03-25)
-------------------------
 * Tweak menu styling

v9.1.63 (2024-03-22)
-------------------------
 * Add open tab event

v9.1.62 (2024-03-22)
-------------------------
 * Make workspace selection use common event pattern
 * Truncate long template name to not break the page
 * Replace iso630 with iso639-lang package
 * Fix non Django 5 compatible code

v9.1.61 (2024-03-21)
-------------------------
 * Support for menu events

v9.1.60 (2024-03-21)
-------------------------
 * Update to latest ruff, isort and djlint
 * Drop TemplateTranslation.comps_as_dict
 * Get rid of channel typed owned sync log views and use new channel view on HTTP log CRUDL
 * Convert templates views to actual CRUDL and fix permissions

v9.1.59 (2024-03-21)
-------------------------
 * Move template code into templates app
 * Stop writing TemplateTranslation.comps_as_dict

v9.1.58 (2024-03-20)
-------------------------
 * Some fixes for on-device mobile issues
 * Allow returning of components in list format from API endpoint
 * Update to latest black
 * Don't try to extract parameters from template url button component display values

v9.1.57 (2024-03-20)
-------------------------
 * Add name field also to template components
 * Tweak template list page to use components list instead of comps_as_dict

v9.1.56 (2024-03-19)
-------------------------
 * Save TemplateTranslation.components as list, use comps_as_dict for API endpoint

v9.1.55 (2024-03-19)
-------------------------
 * Add temporary TemplateTranslation.comps_as_dict field

v9.1.54 (2024-03-19)
-------------------------
 * Add type to template components
 * Remove deprecated fields from template translations

v9.1.53 (2024-03-18)
-------------------------
 * Fix mobile notice

v9.1.52 (2024-03-18)
-------------------------
 * Don't migrate flows when listing campaign events

v9.1.51 (2024-03-17)
-------------------------
 * Tweaks to make the interface more mobile friendly

v9.1.50 (2024-03-17)
-------------------------
 * Better feedback when editing contact fields

v9.1.49 (2024-03-15)
-------------------------
 * Add url param type for buttons with URLs

v9.1.48 (2024-03-14)
-------------------------
 * Show more components for WA templates list
 * Add display to WA templates button components

v9.1.47 (2024-03-14)
-------------------------
 * Remove old templates API endpoint
 * Update flow version for campaigns events single message flows

v9.1.46 (2024-03-13)
-------------------------
 * Reduce WA template sync error logging to ignore those in http logs

v9.1.45 (2024-03-12)
-------------------------
 * Fix the size limit for contact exports

v9.1.44 (2024-03-12)
-------------------------
 * Drop old export models and assets app

v9.1.43 (2024-03-11)
-------------------------
 * Data migration to delete old flow results exports
 * Data migration to delete old msgs exports

v9.1.42 (2024-03-11)
-------------------------
 * Data migration to delete old contacts exports

v9.1.41 (2024-03-11)
-------------------------
 * Mark templates with button URLs and attachment in header not supported
 * Convert exports to use shared export modal view

v9.1.40 (2024-03-08)
-------------------------
 * Allow more WhatsApp templates to be usable in the flows

v9.1.39 (2024-03-07)
-------------------------
 * Updated editor with sendmsg update fix
 * Improve contact export modal and use mailroom endpoint to know how many contacts will be exported

v9.1.38 (2024-03-07)
-------------------------
 * Updated component button rendering

v9.1.37 (2024-03-07)
-------------------------
 * Do not sync templates for channels on suspended orgs or inactive orgs
 * Redact WA password config in HTTP logs

v9.1.36 (2024-03-06)
-------------------------
 * Bump spec version to 13.4
 * Update editor to support template components

v9.1.35 (2024-03-06)
-------------------------
 * Restrict exports of contact groups that are too big
 * Redact auth tokens from http logs when fetching whatsapp templates
 * Cleanup code for fetching whatsapp templates and only create incidents after 5 failures
 * Add data migration to delete old ticket exports

v9.1.34 (2024-03-04)
-------------------------
 * Update floweditor

v9.1.33 (2024-03-04)
-------------------------
 * Bump current flow spec version to 13.3
 * Ensure incidents are ended when releasing a channel

v9.1.32 (2024-03-04)
-------------------------
 * Update temba-components
 * Always send verification email with branding of current org
 * Add incident for WhatsApp templates sync failed

v9.1.31 (2024-02-28)
-------------------------
 * Fix editing user when language is not an option

v9.1.30 (2024-02-28)
-------------------------
 * Hide UI language options when there aren't any
 * Update test_db templates

v9.1.29 (2024-02-27)
-------------------------
 * Remove DS from available channel and only accessible to beta group
 * Prevent further creation of surveyor users since that functionality no longer works

v9.1.28 (2024-02-22)
-------------------------
 * Store servicing flag in session to avoid needing user orgs in context processor
 * Add select_related to user loading for sessions and API tokens
 * Bump cryptography from 42.0.2 to 42.0.4

v9.1.27 (2024-02-21)
-------------------------
 * Update floweditor

v9.1.26 (2024-02-18)
-------------------------
 * Bump cryptography from 42.0.0 to 42.0.2
 * Improve the form for setting flow SMTP and make reusable

v9.1.25 (2024-02-14)
-------------------------
 * Update temba-components

v9.1.24 (2024-02-12)
-------------------------
 * Use dict for flow type icons instead of nested if elses
 * Simplify export finished notification emails
 * Use Org.Export for flows results exports

v9.1.23 (2024-02-09)
-------------------------
 * Fix org avatar scale for menu
 * Fix widget for user avatar

v9.1.22 (2024-02-08)
-------------------------
 * Fix croppie dependency
 * Prefetch user settings on users endpoint

v9.1.21 (2024-02-08)
-------------------------
 * Make user settings one to one

v9.1.20 (2024-02-08)
-------------------------
 * Use orgs.Export for messages exports
 * Simplify sending template emails
 * Add new endpoint to internal API for templates
 * Trim old export and notifications
 * Add support for user avatars

v9.1.19 (2024-02-07)
-------------------------
 * Save transformed components for WA templates

v9.1.18 (2024-02-06)
-------------------------
 * Cleanup flow SMTP formax and show parent settings as default to match mailroom changes
 * Remove old code for saving SMTP into org config

v9.1.17 (2024-02-06)
-------------------------
 * Data migration to backfill Org.flow_smtp

v9.1.16 (2024-02-06)
-------------------------
 * Add new dedicated Org.flow_smtp field for email settings

v9.1.15 (2024-02-06)
-------------------------
 * Bump cryptography from 41.0.7 to 42.0.0
 * Simplify getting default flow email address

v9.1.14 (2024-01-30)
-------------------------
 * Remove using readonly DB connection for fetching groups and fields

v9.1.13 (2024-01-29)
-------------------------
 * Simplify how we check for existing running exports
 * Dta migration to mark old notifications as seen
 * Improve export download page
 * Allow marking all notifications as read by DELETE request to notifications endpoint
 * Use orgs.Export for contact exports

v9.1.12 (2024-01-23)
-------------------------
 * Tweak mailgun channel claiming

v9.1.11 (2024-01-18)
-------------------------
 * Some cleanup to new exports framework

v9.1.10 (2024-01-18)
-------------------------
 * Add skeleton staff only mailgun channel type
 * Add export download view

v9.1.7 (2024-01-18)
-------------------------
 * Update temba-components
 * Save storage path on exports and fix ticket exports not having a download URL

v9.1.6 (2024-01-18)
-------------------------
 * Add new generic orgs.Export model and replace ExportTicketsTask
 * Simplify messaging when export is started

v9.1.5 (2024-01-15)
-------------------------
 * Allow webchat channels to have new convo triggers
 * Finished exports should record number of items exported

v9.1.4 (2024-01-12)
-------------------------
 * Add skeleton temba chat channel type

v9.1.3 (2024-01-12)
-------------------------
 * Add notification for flow exports

v9.1.2 (2024-01-11)
-------------------------
 * Fix issue with completion input focus

v9.1.1 (2024-01-11)
-------------------------
 * Update notification text

v9.1.0 (2024-01-11)
-------------------------
 * Add notifications to UI
 * Fix test_db command
 * Update stable versions in README

v9.0.0 (2024-01-05)
-------------------------
 * Test against mailroom v9
 * Replace dummy migrations with real squashed migrations

v8.3.123 (2024-01-05)
-------------------------
 * Add empty versions of squashed migrations

v8.3.122 (2024-01-04)
-------------------------
 * Update to latest editor

v8.3.121 (2024-01-04)
-------------------------
 * Update to latest floweditor with open ticket changes

v8.3.120 (2024-01-03)
-------------------------
 * Allow ticket body to be optional

v8.3.119 (2024-01-03)
-------------------------
 * Drop ticketer model

v8.3.118 (2024-01-03)
-------------------------
 * Remove view of http logs by ticketer
 * Drop Ticket.ticketer and HTTPLog.ticketer

v8.3.117 (2024-01-03)
-------------------------
 * Remove ticketer types

v8.3.116 (2024-01-03)
-------------------------
 * Fix editor routing edge case
 * Remove ticketers API endpoint

v8.3.115 (2024-01-02)
-------------------------
 * Update to latest flow editor
 * Drop index on ticket.external_id

v8.3.114 (2024-01-02)
-------------------------
 * Stop exposing ticket ticketer on endpoints

v8.3.113 (2024-01-02)
-------------------------
 * Update temba-components
 * Finish cleaning up API v2 tests to use APITestMixin

v8.3.112 (2023-12-14)
-------------------------
 * ContactChat with less padding

v8.3.111 (2023-12-14)
-------------------------
 * Introduce footer

v8.3.110 (2023-12-13)
-------------------------
 * Add index to help fetching scheduled event fires and another to find template translations by channel+external id

v8.3.109 (2023-12-13)
-------------------------
 * Move last indexews from SQL file into Django models and drop unused

v8.3.108 (2023-12-12)
-------------------------
 * Move all remaining flowrun and flowsession indexes onto their models

v8.3.107 (2023-12-12)
-------------------------
 * Fix channel log display when missing URN
 * Queued message treatment, flow editor fix
 * Update poetry deps
 * Move more indexes onto models and remove unnecessary one

v8.3.106 (2023-12-11)
-------------------------
 * Cleanup indexes for FlowStartCount, SystemLabelCount and ContactGroupCount
 * Use datetime timezone aliased as tzone
 * Update django timezone field to 6.1.0

v8.3.105 (2023-12-07)
-------------------------
 * Email changes should reset email status to unverified

v8.3.104 (2023-12-07)
-------------------------
 * Remove duplication between channel read and chart views
 * Cleanup indexes in channels app
 * Remove unhelpful index on eventfire and move other into Django model

v8.3.103 (2023-12-05)
-------------------------
 * Data migration to fix bad last seen on values
 * Add support for user to start the email verification and send themselves the verification link

v8.3.102 (2023-11-30)
-------------------------
 * Testing auto-versioning again

v8.3.99 (2023-11-29)
-------------------------
 * Fix syncing OTP utility templates
 * Drop unused TemplateTranslate.language and country fields

v8.3.98 (2023-11-29)
-------------------------
 * Fix mailroom DB templates components structure
 * Bump cryptography from 41.0.4 to 41.0.6
 * Stop writing TemplateTranslation.language and country and remove unsupported language as a possibility

v8.3.97 (2023-11-28)
-------------------------
 * Stop reading from TemplateTranslation.language and country
 * Undocument the templates API endpoint and add locale field to translations
 * Fix syncing OTP utility templates

v8.3.96 (2023-11-27)
-------------------------
 * Migration to backfill TemplateTranslation.locale and external_locale

v8.3.95 (2023-11-27)
-------------------------
 * Add TemplateTranslation.locale and .external_locale to replace language and country
 * Support saving components and params to message templates

v8.3.94 (2023-11-23)
-------------------------
 * Update temba-components

v8.3.93 (2023-11-23)
-------------------------
 * Fix IVR simulation

v8.3.92 (2023-11-22)
-------------------------
 * Tweak appearance of API explorer

v8.3.91 (2023-11-21)
-------------------------
 * Cleanup API docs

v8.3.90 (2023-11-17)
-------------------------
 * Add pillow dependency

v8.3.89 (2023-11-15)
-------------------------
 * Don't allow oeverwriting of flows with a different type during imports
 * Enforce unique addresses for more channel types

v8.3.88 (2023-11-14)
-------------------------
 * Expose org.input_collation on languages formax
 * Remove blog redirect pattern and sitemap
 * Add unique_address to channel type and use that to validate channel is unique before claiming it

v8.3.87 (2023-11-13)
-------------------------
 * Data migration to delete schedules attached to deleted triggers
 * Simulator should use workspace collation setting
 * Don't include email only notifications in unseen count for UI

v8.3.86 (2023-11-13)
-------------------------
 * Update mailroom endpoint names

v8.3.85 (2023-11-10)
-------------------------
 * Data migration to pause schedules of existing archived triggers

v8.3.84 (2023-11-09)
-------------------------
 * Allow schedules to be paused when triggers are archived

v8.3.83 (2023-11-09)
-------------------------
 * Fix login redirection to next param
 * Drop no longer used fields on Schedule and Label
 * Overrride mailroom URL in mailroom_db command
 * Add view to verify email

v8.3.82 (2023-11-08)
-------------------------
 * Ensure that schedules are actually deleted when a broadcast or trigger is soft deleted
 * Fix trigger list keyword search
 * Make Notifications.medium non-null and use to filter notifications on API endpoint
 * Make deprecated fields o schedule nullable
 * Remove unused ScheduleCRUDL

v8.3.81 (2023-11-07)
-------------------------
 * Add data migration to backfill Notification.medium
 * Add data migration to actually delete inactive schedules

v8.3.80 (2023-11-07)
-------------------------
 * Fix constraint on Trigger to allow deleting of schedules
 * Add medium field Notification to let us model notifications which should be email only

v8.3.79 (2023-11-07)
-------------------------
 * Add data migration to delete ended and orphaned schedules
 * Remove no longer used flow_type field on queued flow starts

v8.3.78 (2023-11-02)
-------------------------
 * Update scheduled broadcast to send now

v8.3.77 (2023-11-01)
-------------------------
 * Move optins inside compose widget

v8.3.76 (2023-11-01)
-------------------------
 * Fix org start view when org isn't set
 * Add data migration to remove scheduled triggers without a schedule and constraint to prevent new ones
 * Fix not showing non-field errors on wizard forms

v8.3.75 (2023-10-31)
-------------------------
 * Remove register "trigger" type
 * Add user settings fields for email verification
 * Update trigger type icons
 * Allow staff to add users
 * Add send broadcast and start flow bulk actions to contact group page

v8.3.74 (2023-10-30)
-------------------------
 * Update temba-components with attachment rendering

v8.3.73 (2023-10-30)
-------------------------
 * Add quick replies to broadcasts

v8.3.72 (2023-10-27)
-------------------------
 * Make sure the missing external ID we make for D360 channels is truncated to 64 characters
 * Un-gate optins
 * Add support for Facebook login for business configurations
 * Move API token formax to Account section

v8.3.71 (2023-10-26)
-------------------------
 * Consistent brand references in templates

v8.3.70 (2023-10-26)
-------------------------
 * Merge pull request #4930 from nyaruka/use-org-brand-domain
 * Remove brand link
 * Replace all brand link with brand domain use

v8.3.69 (2023-10-26)
-------------------------
 * Use org brand domain instead of link
 * Update to use Facebook API v18.0

v8.3.67 (2023-10-26)
-------------------------
 * Update revisions url

v8.3.66 (2023-10-25)
-------------------------
 * Simplify brands

v8.3.65 (2023-10-25)
-------------------------
 * Fix and cleanup view for accepting invitations

v8.3.64 (2023-10-25)
-------------------------
 * Fix start views for agent users
 * Allow agent users to access account settings page
 * Move two factor views out of main menu and into the account view

v8.3.63 (2023-10-23)
-------------------------
 * Fix SendBroadcast action to work with localized compose

v8.3.62 (2023-10-23)
-------------------------
 * Make Trigger.priority non-null and use for ordering

v8.3.61 (2023-10-23)
-------------------------
 * Add data migration to backfill Trigger.priority

v8.3.60 (2023-10-23)
-------------------------
 * Add Trigger.priority and start writing

v8.3.59 (2023-10-20)
-------------------------
 * Fix maxlength for campaign events and focus on compose

v8.3.58 (2023-10-19)
-------------------------
 * Allow triggers to wrap

v8.3.57 (2023-10-19)
-------------------------
 * Update oxford template filter to allow different conjunctions
 * Move all trigger type templates into their own folders
 * Add data migration to merge compatible keyword triggers

v8.3.56 (2023-10-18)
-------------------------
 * Improve display of triggers on list pages
 * Support multiple keywords per trigger in UI
 * Fix WA legacy config page

v8.3.55 (2023-10-17)
-------------------------
 * Show urns properly for urn change events
 * Use localized validation errors for import validation
 * Support multi-keyword triggers in exports and imports

v8.3.54 (2023-10-17)
-------------------------
 * Drop Trigger.keyword

v8.3.53 (2023-10-17)
-------------------------
 * Fix fetching of keywords across triggers when editing a flow

v8.3.52 (2023-10-17)
-------------------------
 * Stop writing Trigger.keyword

v8.3.51 (2023-10-17)
-------------------------
 * Only read from Trigger.keywords

v8.3.50 (2023-10-16)
-------------------------
 * Make ticketer nullable on ticket
 * Convert tickets API endpoints to use CRUDL perms
 * Make sure we show the issue icon on the flow list page

v8.3.49 (2023-10-13)
-------------------------
 * Add data migration to populate keywords on trigger
 * Add localization to create broadcast wizard

v8.3.47 (2023-10-12)
-------------------------
 * Add Trigger.keywords and start writing
 * Switch contacts API endpoints to use CRUDL perms
 * Cleanup BroadcastCRUDL.Send which is now only for sending to a flow node
 * Remove unused LabelCRUDL.List view
 * Convert messages, media and label API endpoints to use CRUDL perms

v8.3.46 (2023-10-11)
-------------------------
 * Remove no longer needed deprecated options on definitions endpoint
 * Replace orgs.org_api permission
 * Drop no longer used fields on FlowRevision

v8.3.45 (2023-10-10)
-------------------------
 * Show exclusion groups on trigger list pages
 * Fix updating keyword triggers for flows
 * Make sure we display trigger channel if set
 * Limit access to API explorer to editors and admins
 * Convert resthook API endpoints to use CRUDL based permissions

v8.3.44 (2023-10-06)
-------------------------
 * Allow request optin if optins exist
 * Fix blurb for opt-out trigger
 * Remove last usages of FlowLabel.parent and FlowRevision.modifiy_by
 * Switch optins, topics, ticketers and templates API endpoints to use CRUDL perms
 * Replace brand specific flow users with a single system user

v8.3.43 (2023-10-05)
-------------------------
 * Update editor and components

v8.3.42 (2023-10-05)
-------------------------
 * Make channel on trigger forms clearable
 * Prepare unused fields on FlowRevision for removal and change all models in flows app to use orgs.User
 * Allow beta testers to access optin features
 * Switch flows, flow_starts and runs API endpoints to use CRUDL permissions
 * Add optional channel field to call triggers types that are based on channel activity

v8.3.41 (2023-10-04)
-------------------------
 * Add optin as field to channelevents
 * Allow perms to be made API specific so that we can limit agent access to the UI

v8.3.40 (2023-10-03)
-------------------------
 * Remove globals from agent store when missing permission
 * Remove arst

v8.3.39 (2023-10-03)
-------------------------
 * Fix compose clear on send
 * Use more CRUDL perms with API endpoints

v8.3.38 (2023-10-03)
-------------------------
 * Remove completion from contact chat
 * Do not recreate the events when the campaign is archived

v8.3.37 (2023-10-02)
-------------------------
 * Abstract functionality for triggers based on channel actvity into base classes
 * API endpoint should default to CRUDL based permissions if permission not specified
 * Update to use Facebook API v17

v8.3.36 (2023-09-29)
-------------------------
 * Remove minutes label from channel chart
 * Add workspace breakdown for dashboard

v8.3.35 (2023-09-28)
-------------------------
 * Update opt-in styling
 * Fix generation of history events from messages with optins

v8.3.34 (2023-09-28)
-------------------------
 * Fix migration conflict

v8.3.33 (2023-09-28)
-------------------------
 * Fix rendering of optin triggers
 * Completely remove channel alerts

v8.3.32 (2023-09-27)
-------------------------
 * Fix previous accidental merge to main to add optin import support
 * Cleanup views accessing request org
 * Add optin as option to broadcast create wizard

v8.3.30 (2023-09-27)
-------------------------
 * Allow the target_urls of incident notifications to differ by type
 * Use proper secret generation for recovery tokens and re-org code
 * Fix task discover for legacy whatsapp channel type
 * Implement channel disconnected alert as incident

v8.3.29 (2023-09-26)
-------------------------
 * Update editor to include opt-ins

v8.3.28 (2023-09-26)
-------------------------
 * Fix Contact Importss
 * Rename old legacy channel types
 * Add title to incident list page and tweak styling
 * Implement email notifications for incidents
 * Fix ticket squashable count models

v8.3.27 (2023-09-25)
-------------------------
 * Tweak mailroom_db to create an FBA channel instead of a TWT channel
 * Remove ticketers as a feature and the views for connecting external ticketers
 * Re-add optin as distinct message type
 * Add undocumented API endpoint for opt-ins

v8.3.26 (2023-09-22)
-------------------------
 * Bump cryptography from 41.0.3 to 41.0.4
 * Add optin field to Broadcast

v8.3.25 (2023-09-21)
-------------------------
 * Fix trigger ordering

v8.3.24 (2023-09-21)
-------------------------
 * Add opt-in and opt-out trigger types (staff only for now)
 * Group keyword triggers and catch all triggers under a Messages folder
 * Move broadcasts and scheduled to their own pages

v8.3.23 (2023-09-21)
-------------------------
 * Replace Msg.type=optin with optin reference on msg
 * Group trigger types into folders
 * Make sure staff can update the log policy on all channel types

v8.3.22 (2023-09-19)
-------------------------
 * Make ticketers API endpoint unpublicized
 * Add 'Send Now' to broadcast creation

v8.3.21 (2023-09-18)
-------------------------
 * Add basic OptIn model
 * Use env variable for dev mode host

v8.3.20 (2023-09-12)
-------------------------
 * Update editor for localized attachment fix

v8.3.19 (2023-09-12)
-------------------------
 * Add new data migration to fix IVR call counts
 * Drop Channel.parent, ContactURN.auth and Org.input_cleaners
 * Remove support for delegate channels

v8.3.18 (2023-09-07)
-------------------------
 * Add data migration to populate ContactURN.auth_tokens

v8.3.17 (2023-09-06)
-------------------------
 * Add ContactURN.auth_tokens to replace .auth

v8.3.16 (2023-09-06)
-------------------------
 * Tweak documentation for flow_starts endpoint
 * Allow agents to update tickets topics

v8.3.15 (2023-09-06)
-------------------------
 * Add hover-darker button option
 * Update icons

v8.3.14 (2023-08-31)
-------------------------
 * Limit to load the recent 100 sessions
 * Disallow GET request for media upload view

v8.3.13 (2023-08-28)
-------------------------
 * Tweaks to the channel config blurbs for consistency
 * Fetching messages by label should include arched messages
 * Use secrets module instead of random for random_string
 * Little bit of cleanup in channel types like removing unused fields

v8.3.12 (2023-08-23)
-------------------------
 * Add ChannelType.config_ui to replace configuration_urls, configuration_blurb etc
 * Show Somleng config URLs based on channel role
 * Add Org.input_collation
 * Remove Blackmnyna, Chikka, Junebug, Twitter legacy, old Zenvia channel types

v8.3.11 (2023-08-17)
-------------------------
 * Convert final haml templates in root directory

v8.3.10 (2023-08-17)
-------------------------
 * Add Org.input_cleaners
 * Always show name / anon id for anon orgs in contact lists
 * Don't let mailroom handle tasks during tests
 * Fix title on welcome page

v8.3.9 (2023-08-16)
-------------------------
 * Fix onSpload fire when initial page doesn't call it

v8.3.8 (2023-08-16)
-------------------------
 * Use $ instead of onSpload

v8.3.7 (2023-08-16)
-------------------------
 * Fix Javascript on claim number view
 * Switch test_db to assume a docker container

v8.3.6 (2023-08-15)
-------------------------
 * Convert haml templates in includes folder and utils app
 * Cleanup page titles in settings section

v8.3.5 (2023-08-14)
-------------------------
 * Convert haml templates in public and orgs apps

v8.3.4 (2023-08-14)
-------------------------
 * Convert templates in assets, channels, msgs, request_logs and schedules apps as well as overridden smartmin templates

v8.3.3 (2023-08-10)
-------------------------
 * Simplify message indexes and system label queries

v8.3.2 (2023-08-10)
-------------------------
 * Add data migration to convert old I/F msg types

v8.3.1 (2023-08-09)
-------------------------
 * Merge pull request #4779 from nyaruka/less_haml
 * Some tweaks to templates based on linter
 * Convert all haml templates in channel types

v8.3.0 (2023-08-09)
-------------------------
 * Drop no longer used Org.brand field
 * Add messagebird channel type

v8.2.0 (2023-08-07)
-------------------------
 * Update stable versions

v8.1.245 (2023-08-05)
-------------------------
 * Truncate query lables on flow start
 * Fix line length formatting
 * Fixes for login and API titles

v8.1.244 (2023-08-04)
-------------------------
 * Fix error handling for temba-contact-search

v8.1.243 (2023-08-03)
-------------------------
 * Fix DELETE endpoints in API explorer
 * Bump cryptography from 41.0.2 to 41.0.3

v8.1.242 (2023-08-02)
-------------------------
 * Update to components with modax serialize fix

v8.1.241 (2023-08-02)
-------------------------
 * Fix two factor disable and initial QR code rendering

v8.1.240 (2023-08-01)
-------------------------
 * Update components with checkbox value update
 * Stop writing no longer used Org.brand

v8.1.239 (2023-08-01)
-------------------------
 * Temp fix for org export page by replacing temba-checkbox with regular inputs
 * Cleanup msg_console

v8.1.238 (2023-07-28)
-------------------------
 * Fix flow start log when starts don't have exclusions
 * Remove unnecessary CSS class to hover

v8.1.237 (2023-07-28)
-------------------------
 * Only consider the parsed query string in contact_search clean
 * Add show CSS class to icon for contact list sorting

v8.1.236 (2023-07-27)
-------------------------
 * Rename flow_broadcast to flow_start
 * Update editor to fix cases on result split
 * Add new channel log types used by courier
 * Update contact search widget for flow starts

v8.1.235 (2023-07-26)
-------------------------
 * Convert templates in dashboard, docs, globals, ivr, locations and notifications apps
 * Use title-text for just overriding the text
 * Restore missing msg box templates

v8.1.234 (2023-07-25)
-------------------------
 * Fix org export page
 * Fix permissions for viewer for flow results

v8.1.233 (2023-07-25)
-------------------------
 * Simpliy convert_templates script
 * Consistent title for initial page load
 * Remove spa-title and spa-style
 * Add archives to STORAGES

v8.1.232 (2023-07-24)
-------------------------
 * Do not set the max for y axis chart to allow that to be calculated
 * Convert templates in the triggers app from haml

v8.1.231 (2023-07-21)
-------------------------
 * Simplify redis settings and organize settings better in sections

v8.1.230 (2023-07-20)
-------------------------
 * Tweak system check for storage settings to check different storages are configured
 * Convert S3 log access to be via django storages
 * Use pg_dump/restore from docker container in mailroom_db command so it's always correct version

v8.1.229 (2023-07-19)
-------------------------
 * Fix tickets list, to show compose properly on Firefox
 * Add cpAddress parameter as optional for MTN channel type

v8.1.228 (2023-07-18)
-------------------------
 * Update Instagram docs broken link
 * Allow initiating flow results download form the the flow labels filter view

v8.1.227 (2023-07-17)
-------------------------
 * Bump cryptography from 41.0.0 to 41.0.2

v8.1.226 (2023-07-13)
-------------------------
 * Rework trimming cron tasks to use delete_in_batches
 * Drop no longer used Binary Optional Data field

v8.1.225 (2023-07-13)
-------------------------
 * Fix icon for globals delete
 * Migrate old Twilio channels using .bod to use .config instead
 * Remove duplicate menu views in classifiers and channels apps

v8.1.224 (2023-07-12)
-------------------------
 * Add log_policy to channel

v8.1.223 (2023-07-11)
-------------------------
 * More tweaks to org deletion

v8.1.222 (2023-07-11)
-------------------------
 * Add delete_in_batches util function to improve org deletion
 * Actually fix deletion of campaign events during org deletion

v8.1.221 (2023-07-11)
-------------------------
 * Fix deleting of campaign events and add more logging to org deletion

v8.1.220 (2023-07-10)
-------------------------
 * Delete is only for deleting child workspaces

v8.1.219 (2023-07-10)
-------------------------
 * Fix problems with org deletion

v8.1.218 (2023-07-07)
-------------------------
 * Update to flow editor with fix for ward cases

v8.1.217 (2023-07-06)
-------------------------
 * Convert haml files in contacts app
 * Bump django from 4.2.2 to 4.2.3

v8.1.216 (2023-07-05)
-------------------------
 * Add data migration to fix archived message counts for labels
 * Convert haml templates in campaigns and classifiers apps

v8.1.215 (2023-07-05)
-------------------------
 * Add missing migration that rebuilds constraint on contact URNs
 * Update channel log retention to 2 weeks
 * Disable old 360 Dilalog channel type, and take the new integration out of beta

v8.1.214 (2023-07-03)
-------------------------
 * Update to psycopg3 non-binary
 * Reference templates as html

v8.1.213 (2023-07-03)
-------------------------
 * Convert flows app to be hamless

v8.1.212 (2023-07-03)
-------------------------
 * Sorted group list when editing contacts
 * Switch channel charts to load with json instead of embedded data

v8.1.211 (2023-06-28)
-------------------------
 * Fix Twilio channel update modal

v8.1.210 (2023-06-28)
-------------------------
 * Fix mangling of option attributes
 * Save channel logs with channels/ prefix
 * Add configurable agent access per contact field

v8.1.209 (2023-06-28)
-------------------------
 * Fix creating PublicFileStorage

v8.1.208 (2023-06-28)
-------------------------
 * Fix S3 channel logs paths to not start with slash
 * Update to Django 4.2

v8.1.207 (2023-06-27)
-------------------------
 * Convert some haml templates to html

v8.1.206 (2023-06-27)
-------------------------
 * Drop duplicate index
 * Look for channel logs in S3 when not found in database
 * Move tracking label counts to statement level triggers

v8.1.205 (2023-06-27)
-------------------------
 * Replace index on channellog.channel

v8.1.204 (2023-06-26)
-------------------------
 * Fix inline group created and broadcast action

v8.1.203 (2023-06-26)
-------------------------
 * Update contact action fix

v8.1.202 (2023-06-26)
-------------------------
 * Rework settings for S3 buckets

v8.1.201 (2023-06-23)
-------------------------
 * Support runtime locales in components

v8.1.200 (2023-06-23)
-------------------------
 * Update for flow editor text inputs with null values

v8.1.199 (2023-06-22)
-------------------------
 * Updates for select widget to behave with more standard form controls

v8.1.198 (2023-06-22)
-------------------------
 * Rollback components

v8.1.197 (2023-06-22)
-------------------------
 * Override the correct alpha3 code for Oromifa
 * Update form components to use element internals
 * Rework loading of channel logs so easier to fetch from S3 too

v8.1.196 (2023-06-21)
-------------------------
 * Improve ExternalURLField and don't assume http
 * Use org import task to import flows

v8.1.195 (2023-06-19)
-------------------------
 * Name override for oro language
 * Remove no longer used code relating to contact fields

v8.1.194 (2023-06-19)
-------------------------
 * Don't ignore user provided role for somleng shortcodes
 * Fix flow export button height
 * Fix import translation to use new UI
 * Fix parent ID lookup in import geojson
 * Support Dialog360 Cloud API channels

v8.1.193 (2023-06-14)
-------------------------
 * Add surveyor icon

v8.1.192 (2023-06-14)
-------------------------
 * Add icons for flows, fix issue with some spload fires

v8.1.191 (2023-06-13)
-------------------------
 * Broadcast update via wizard and updated list styling

v8.1.190 (2023-06-12)
-------------------------
 * Add agent_access to API fields endpoint
 * Restrict agent users view of field values on API contacts endpoint
 * Remove use of django tags inside javascript

v8.1.189 (2023-06-12)
-------------------------
 * Fix broken list view template
 * Add djlint and latest django-hamlpy

v8.1.188 (2023-06-09)
-------------------------
 * Tweak contact field access backfill migration

v8.1.187 (2023-06-09)
-------------------------
 * Add ContactField.agent_access and backfill to view
 * Use statement level triggers for tracking current node counts
 * Remove old scheduled broadcast create view

v8.1.186 (2023-06-08)
-------------------------
 * Format api_root.html and fix errors
 * Fix channel log pretty printing

v8.1.183 (2023-06-08)
-------------------------
 * Add djLint config
 * Add basic wizard support

v8.1.182 (2023-06-08)
-------------------------
 * Support imports with Status column
 * Make viewer role users a feature that can be toggled
 * Allow exporting of blocked, stopped and archived contacts

v8.1.181 (2023-06-07)
-------------------------
 * Add redact_values for FBA and IG channel types
 * Remove unused code for legacy UI contact read and list pages
 * Rework channel log anonymization so even staff users have to explicitly break out of it
 * Rework channel log rendering to start from JSONified version
 * Fix adding queued braodcasts to Outbox view and counts
 * Cleanup db triggers for broadcasts

v8.1.180 (2023-06-05)
-------------------------
 * Fix failed message resending and archived message deletion

v8.1.179 (2023-06-05)
-------------------------
 * Drop ChannelLog.msg and .call

v8.1.178 (2023-06-05)
-------------------------
 * Bump cryptography from 39.0.2 to 41.0.0
 * Stop reading from ChannelLog.msg and .call
 * Use per-statement db triggers for system label counts

v8.1.177 (2023-06-02)
-------------------------
 * Remove dupe from changelog

v8.1.176 (2023-06-02)
-------------------------
 * Add some blocks on main templates

v8.1.175 (2023-06-02)
-------------------------
 * Add select all on list pages

v8.1.174 (2023-06-01)
-------------------------
 * Noop when releasing an already released org
 * Rework and simplify channel count db triggers

v8.1.173 (2023-06-01)
-------------------------
 * Remove support for filtering channel logs by folder

v8.1.171 (2023-05-31)
-------------------------
 * Add index on channellog.uuid
 * Impove and expose the call list view

v8.1.170 (2023-05-31)
-------------------------
 * Remove rendering of contact history as template now that new UI only consumes it as JSON
 * Fix inbox msg type for Android channels

v8.1.169 (2023-05-30)
-------------------------
 * Allow call count backfill migration to be called offline
 * Fix ivr call trigger migration
 * Remove unused stuff from inbox views

v8.1.168 (2023-05-30)
-------------------------
 * Add data migration to backfill ivr call counts

v8.1.167 (2023-05-29)
-------------------------
 * Add DB triggers to track counts of calls as a new system label

v8.1.166 (2023-05-29)
-------------------------
 * Stop writing SystemLabelCount.is_archived so it can be dropped

v8.1.165 (2023-05-29)
-------------------------
 * Always write system label counts with is_archived=False and make field nullable

v8.1.164 (2023-05-29)
-------------------------
 * Add data migration to delete old system label counts for is_archived=true because they're no longer updated
 * Fix getting FB business ID for WAC channels

v8.1.163 (2023-05-25)
-------------------------
 * Return empty sample/fields on preview_start endpoint until contactsearch component is updated

v8.1.162 (2023-05-25)
-------------------------
 * Add BroadcastCRUDL.Preview
 * Fix broadcast send history template

v8.1.161 (2023-05-24)
-------------------------
 * User orgs based on request
 * Switch brand array to dict
 * Move plivo connect view to channel type

v8.1.160 (2023-05-19)
-------------------------
 * Fix field update and deleting with same key

v8.1.159 (2023-05-19)
-------------------------
 * Don't allow horizontal scroll by default

v8.1.158 (2023-05-19)
-------------------------
 * Fix scrolling for content pages without full height
 * Tweak how we run python scripts in CI

v8.1.157 (2023-05-18)
-------------------------
 * Add ticket editing
 * Remove old ticket assign view and support for notes with assignment
 * Add ticket topic menu and resizer
 * Move WAC connect view to the WhatsApp cloud channel type package
 * Remove accounts formax from workspace view as it isn't needed with new UI

v8.1.156 (2023-05-17)
-------------------------
 * Update components for 302 fix
 * Make post_url work identically to posterize

v8.1.155 (2023-05-17)
-------------------------
 * Better handling of post_url for spa content menu
 * Really fix hiding surveyor form

v8.1.154 (2023-05-17)
-------------------------
 * Hide the surveyor password input and not just the help texti
 * Fix URLs in JS files

v8.1.153 (2023-05-17)
-------------------------
 * Move channel type constants to the channel type class
 * Don't show option to enter surveyor password if surveyor feature not enabled
 * Scoped javascript for flow broadcast modal

v8.1.152 (2023-05-15)
-------------------------
 * Make js function name unique
 * Fix no_nav extra-script blocks

v8.1.151 (2023-05-15)
-------------------------
 * Fix the API explorer scripts and styles blocks

v8.1.150 (2023-05-15)
-------------------------
 * Cleanup broken or unused posterized links
 * Drop old flow start fields

v8.1.149 (2023-05-14)
-------------------------
 * Fix signups

v8.1.148 (2023-05-12)
-------------------------
 * Fix backwards compat for send message to somebody else

v8.1.147 (2023-05-12)
-------------------------
 * Fix flow refresh and global redirect hook

v8.1.146 (2023-05-12)
-------------------------
 * Add some null checks for frame selectors

v8.1.145 (2023-05-11)
-------------------------
 * Fix width for other views and posterize on choose

v8.1.144 (2023-05-11)
-------------------------
 * Fix login width
 * Tweak Somleng claim blurb

v8.1.143 (2023-05-11)
-------------------------
 * Stop reading from old FlowStart fields
 * Merge and clean up main frame
 * Rename Twiml API channel to Somleng

v8.1.142 (2023-05-11)
-------------------------
 * Add base mixin for channel type specific views that gives access to the type class
 * Update components and editor to support compose for somebody else
 * Move vonage connect view to the channel type
 * Allow deleting of archived triggers

v8.1.141 (2023-05-10)
-------------------------
 * Fix contacts title
 * Fix vanilla landing
 * Remove lessblock and replace with compiled css
 * Bump django from 4.1.7 to 4.1.9

v8.1.140 (2023-05-09)
-------------------------
 * Fix ticket padding
 * Remove remaining spa files
 * Add link to reset the latest credentials
 * Preset channel connection

v8.1.139 (2023-05-09)
-------------------------
 * Add blocked icon

v8.1.138 (2023-05-09)
-------------------------
 * Update labeling to use temba-checkbox and remove jQuery
 * Fix trim_channel_logs config and rework so task olny runs for an hour max
 * Change test_db to create single org at a time

v8.1.137 (2023-05-09)
-------------------------
 * Add exclusions and params fields to FlowStart and start writing them

v8.1.136 (2023-05-09)
-------------------------
 * Don't include brand variables in less node

v8.1.135 (2023-05-09)
-------------------------
 * Remove references to old icon set
 * Remove unused jquery bits and intercooler
 * Remove bootstrap

v8.1.134 (2023-05-08)
-------------------------
 * Remove no longer used perms
 * Remove any old non-spa templates not being extended by the spa version
 * Remove is_spa logic from templates
 * Remove old contact update fields views

v8.1.133 (2023-05-05)
-------------------------
 * Add default color

v8.1.132 (2023-05-05)
-------------------------
 * Remove settings turd

v8.1.131 (2023-05-05)
-------------------------
 * Remove old nav from landing page

v8.1.130 (2023-05-04)
-------------------------
 * Remove spa checking in views

v8.1.129 (2023-05-04)
-------------------------
 * Remove JSON view to list notifications now that has moved to the internal API
 * Remove non-spa items from content menus

v8.1.128 (2023-05-03)
-------------------------
 * Fix contact import

v8.1.127 (2023-05-03)
-------------------------
 * Remove support for adding bulk sender delegate channels
 * Remove ability to create IVR delegates for android channels
 * Remove org home view altogether and update links to point to workspace view

v8.1.126 (2023-05-03)
-------------------------
 * Change cookie checking for UI so that we always default to new UI
 * Add color picker widget
 * Remove ability to store twilio credentials on the org

v8.1.125 (2023-05-02)
-------------------------
 * Tweak notifications index to match API endpoint
 * Add new internal API with a notifications endpoint
 * Use DRF defaults for STRICT_JSON and UNICODE_JSON
 * Remove unused .api URL suffixes

v8.1.124 (2023-05-01)
-------------------------
 * Make contact.modify work with new and old format
 * Make ticket a reserved field name

v8.1.123 (2023-04-27)
-------------------------
 * Hide Open Ticket option on contact read page if there's already an open a ticket
 * Rework soft and hard msg deleting to be more performant

v8.1.122 (2023-04-26)
-------------------------
 * Remove db constriants on Msg.flow and Msg.ticket

v8.1.121 (2023-04-26)
-------------------------
 * Tweak migration dependency
 * Show counts of tickets by topic on tickets menu

v8.1.120 (2023-04-25)
-------------------------
 * Add topic counts to the API endpoint
 * Add undocumented param to contacts API endpoint which allows URNs to be expanded
 * Data migration to backfill ticket counts by topic

v8.1.119 (2023-04-25)
-------------------------
 * Start writing ticket counts for topics

v8.1.118 (2023-04-24)
-------------------------
 * Fix deleting of flows and tickets which are referenced by messages
 * Fix pattern match for folder uuid
 * Stop writing TicketCount.assignee

v8.1.117 (2023-04-24)
-------------------------
 * Stop reading from TicketCount.assignee

v8.1.116 (2023-04-21)
-------------------------
 * Add more channel icons

v8.1.115 (2023-04-21)
-------------------------
 * Update icons
 * Add ticket topic folders

v8.1.114 (2023-04-20)
-------------------------
 * Add migration to backfill TicketCount.scope

v8.1.113 (2023-04-20)
-------------------------
 * Add scope field to TicketCount and start writing

v8.1.112 (2023-04-20)
-------------------------
 * Dropdowns for slow clickers
 * Tighten up animations
 * Use services for redis, elastic and postgres in CI

v8.1.111 (2023-04-18)
-------------------------
 * Fix and archive keyword triggers with no match_type

v8.1.110 (2023-04-18)
-------------------------
 * Prefetch flows on message views and make titles consistent

v8.1.109 (2023-04-18)
-------------------------
 * Add links for menu, add flow badge, update label badges
 * Remove Chikka channel type which no longer exists
 * Update mailroom_db command to allow connecting to non-file socket postgres

v8.1.108 (2023-04-17)
-------------------------
 * Add ticket field to msg model

v8.1.107 (2023-04-13)
-------------------------
 * Allow deleting of groups used in triggers

v8.1.106 (2023-04-13)
-------------------------
 * Don't show topics on tickets until clicked

v8.1.105 (2023-04-12)
-------------------------
 * Fix js items on context menus

v8.1.104 (2023-04-11)
-------------------------
 * Do not display schedule events for archived triggers
 * Don't require db superuser for test_db command
 * Make ticket banner expandable

v8.1.103 (2023-04-10)
-------------------------
 * Fix urls when searching and paging
 * Follow message on auto assign for unassigned folder

v8.1.102 (2023-04-10)
-------------------------
 * Add contact details pane, hide empty tabs
 * Auto assign tickets when sending messages
 * Add nicer ticket assignment using temba-contact-tickets component
 * Fix deleting of orgs with incidents

v8.1.101 (2023-04-06)
-------------------------
 * Add field search handler on tickets

v8.1.100 (2023-04-06)
-------------------------
 * Add fields to tickets

v8.1.99 (2023-04-06)
-------------------------
 * Add test util to make it easier to mess with brands
 * Drop Org.stripe_customer_id

v8.1.98 (2023-04-06)
-------------------------
 * Link contact name on tickets to the contact page if permitted
 * Drop Org.plan, plan_start and plan_end

v8.1.97 (2023-04-05)
-------------------------
 * Pull tickets out of contact chat
 * Scheduled messages to broadcasts with compose widget

v8.1.96 (2023-04-03)
-------------------------
 * Stop reading Org.plan and .plan_end
 * Bump redis from 4.5.3 to 4.5.4

v8.1.95 (2023-03-31)
-------------------------
 * Fix temba-store race on load

v8.1.94 (2023-03-29)
-------------------------
 * Bump version of openpyxl

v8.1.93 (2023-03-29)
-------------------------
 * Update Excel reading dependencies

v8.1.92 (2023-03-29)
-------------------------
 * Use unittests.mock.Mock in tests instead of custom mock_object

v8.1.91 (2023-03-28)
-------------------------
 * Upgrade redis library version

v8.1.90 (2023-03-27)
-------------------------
 * NOOP instead of assert if archiving msg which is already archived etc

v8.1.89 (2023-03-27)
-------------------------
 * Do not fail to release channel when missing mtn subscription id in config
 * Add incident type for org suspension

v8.1.88 (2023-03-23)
-------------------------
 * Fix suspending and unsuspending orgs so that it correctly updates children
 * Use a name for the active org that doesn't collide

v8.1.87 (2023-03-23)
-------------------------
 * Manually fix version number

v8.1.86 (2023-03-23)
-------------------------
 * Fix scrolling on WhatsApp templates page

v8.1.85 (2023-03-23)
-------------------------
 * Handle short screens better on run list page

v8.1.84 (2023-03-22)
-------------------------
 * Update to coverage 7.x

v8.1.83 (2023-03-22)
-------------------------
 * Use onSpload to wire handlers on account form

v8.1.82 (2023-03-22)
-------------------------
 * Support setting and removing the subscription URL for MTN channels

v8.1.81 (2023-03-21)
-------------------------
 * Update ruff and isort

v8.1.80 (2023-03-21)
-------------------------
 * Update black

v8.1.79 (2023-03-20)
-------------------------
 * Add mouseover text for temba-date
 * Reload page on org mismatch
 * Use embedded title instead of response header

v8.1.78 (2023-03-20)
-------------------------
 * Add globals to new ui
 * Make it harder to accidentally delete an org
 * Rewrite org deletion test and fix deletion issues

v8.1.77 (2023-03-16)
-------------------------
 * Limit groups to a single line on contact page

v8.1.76 (2023-03-16)
-------------------------
 * Remove unused fields and indexes on broadcast model
 * Reload page on version mismatch
 * Add support for MTN Developer Portal channel

v8.1.75 (2023-03-16)
-------------------------
 * Add menu path for org export and import
 * Fix legacy goto function for old UI
 * Warn users who go back to the old interface
 * Remove support for broadcasts with associated tickets

v8.1.74 (2023-03-15)
-------------------------
 * Show version number on public index page
 * Add poetry plugin to maintain version number in temba/__init__.py
 * Fix textinput inner scrolling

v8.1.73 (2023-03-15)
-------------------------
 * Stop returning type=flow|inbox on messages endpoint
 * Cleanup location app models

v8.1.72 (2023-03-14)
-------------------------
 * Convert Org.config and Channel.config to be real JSON

v8.1.71 (2023-03-14)
-------------------------
 * Strip out invalid HTTP header characters from page title response headers
 * Fix mailroom db command to patch uuid generation after migrations are run
 * Expose flow on messages API endpoint

v8.1.70 (2023-03-13)
-------------------------
 * Broad support for meta click for new tabs
 * Make Org.config and Channel.config non-null

v8.1.69 (2023-03-13)
-------------------------
 * Simplify use of config fields on channel update forms
 * Fix alias editor to use the new UI frame
 * Support updating Twilio credentials for T, TMS and TWA channels

v8.1.68 (2023-03-13)
-------------------------
 * Rework messages and broadcasts API endpoints to accept media ojects UUIDs as attachments
 * Make Msg.uuid and msg_type non-null

v8.1.67 (2023-03-10)
-------------------------
 * Fix layering for menu

v8.1.66 (2023-03-09)
-------------------------
 * Fix initial editor load
 * Schedule message validation

v8.1.65 (2023-03-09)
-------------------------
 * Update endpoints for messages and media

v8.1.64 (2023-03-08)
-------------------------
 * Tweak layout for editor
 * Cleanup fail_old_messages task. Use correct statuses and return number failed.

v8.1.63 (2023-03-08)
-------------------------
 * Adjust export download page for new UI
 * Make media list page (still staff only) filter by org and add index

v8.1.62 (2023-03-08)
-------------------------
 * Small z-index tweak

v8.1.61 (2023-03-07)
-------------------------
 * Tweak simulator placement in new ui

v8.1.60 (2023-03-07)
-------------------------
 * Encourage users to try the new interface
 * Add lightbox for contact history

v8.1.59 (2023-03-07)
-------------------------
 * Rework code depending on msg_type=I|F

v8.1.58 (2023-03-07)
-------------------------
 * Add missing channels migration
 * Use msg.created_by if set in ticket list view
 * Remove SMS type channel alerts

v8.1.57 (2023-03-06)
-------------------------
 * Move index on msg.external_id onto the model

v8.1.56 (2023-03-06)
-------------------------
 * Fix soft deleting of scheduled messages so schedule is deleted too
 * Stop saving JSONAsTextField values as null for empty dicts and lists
 * Update select s3 usage for msg exports to not rely on type=inbox|flow
 * Add created_by to Msg and populate on events in contact histories

v8.1.55 (2023-03-02)
-------------------------
 * Fix import for sync fcm task
 * Create new filters and partial indexes for Inbox, Flows and Archived

v8.1.54 (2023-03-02)
-------------------------
 * Fix enter on compose

v8.1.53 (2023-03-01)
-------------------------
 * Add compose component to contact chat
 * Pixel tweak on contact read page
 * Move more Android relayer code out of Channel

v8.1.52 (2023-03-01)
-------------------------
 * Simplify what we display for Android channels on read page

v8.1.50 (2023-02-28)
-------------------------
 * Make spload universal

v8.1.49 (2023-02-28)
-------------------------
 * Make spload work on formax pages

v8.1.48 (2023-02-28)
-------------------------
 * Add more goto(event)  
 * Fix content differing from page-load vs inline load  
 * Add page title for spa response headers  
 * Clean up subtitles on spa pages  
 * Add link to flow starts (and clean up list page styling)
 * Add link for webhook calls (and cleanup styling here too)  
 * Update styling for log pages for both old / new ui

v8.1.47 (2023-02-27)
-------------------------
 * Be less clever with page titles. Fix label js errors.
 * Make sure tests can run without making requests to external URLs
 * Unpublicize folder=incoming on messages API docs and re-add index with status=H

v8.1.46 (2023-02-23)
-------------------------
 * Fix external links in old ui

v8.1.45 (2023-02-23)
-------------------------
 * Fix external channel links
 * No longer intercept clicks in spa-content
 * Cleanup Channel model fields
 * Fix channel claim  external URLs in new UI

v8.1.44 (2023-02-23)
-------------------------
 * Exclude PENDING messages in contact history and API by org and contact
 * Add -id to msg fetch ordering in Contact.get_history
 * For both messages and tickets, replace the default indexes on org and contact with indexes that match the API ordering

v8.1.43 (2023-02-23)
-------------------------
 * Use statement level db trigger for broadcast msg counts
 * Update django to 4.1.7

v8.1.42 (2023-02-22)
-------------------------
 * Only look at queued messages when syncing android channels
 * Re-add Msg.STATUS_INITIALIZING to use for outgoing messages which fail to queue
 * Include STATUS_ERRORED messages in Outbox views

v8.1.41 (2023-02-22)
-------------------------
 * Remove suprious property

v8.1.40 (2023-02-22)
-------------------------
 * Fix contact imports in new ui
 * Fix menu refresh race
 * Remove window.lastFetch
 * Adjust menu paths for new UI channel views
 * Use SpaMixin to more channels extra views

v8.1.39 (2023-02-22)
-------------------------
 * Move Msg.update into android package
 * Make text optional on broadcasts endpoint (messages need text or attachments)

v8.1.38 (2023-02-21)
-------------------------
 * Fix dashboard not loading when content
 * Fix handling FCM sync failure

v8.1.37 (2023-02-21)
-------------------------
 * Don't lookup related fields in API if lookup value type is wrong
 * Update django 4.0.10
 * Fetching sent folder on messages endpoint should return messages ordered by -sent_on same as UI
 * Exclude unhandled messages from Incoming folder on messages API endpoint
 * More agressive menu refreshing
 * Move much of the old android relayer code into its own package
 * Add media API endpoint, undocumented for now
 * Open up new UI access to everyone

v8.1.36 (2023-02-20)
-------------------------
 * Cleanup use of validators in the API
 * Add support for Msg.TYPE_TEXT to be used (for now) for outgoing messages

v8.1.35 (2023-02-17)
-------------------------
 * Add org start redirection view
 * Convert Attachment to be a dataclass
 * Rework msg write serializer to create a transient Msg instance that the read serializer can use without hitting the db
 * Add unpublicized API endpoint to send a single message
 * Add msg_send to mailroom client

v8.1.34 (2023-02-16)
-------------------------
 * Drop raw_urns field on Broadcast
 * Pass group id instead of uuid to contact_search mailroom endpoint
 * Remove unused expression_migrate from mailroom client

v8.1.33 (2023-02-15)
-------------------------
 * Fix routing of current workspace to settings
 * Add Broadcast.urns which matches the JSON and FlowStart.urns

v8.1.32 (2023-02-14)
-------------------------
 * Drop Broadcast.urns and .send_all

v8.1.30 (2023-02-13)
-------------------------
 * Fix keyword triggers match type

v8.1.29 (2023-02-13)
-------------------------
 * Fix omnibox search for anon org to allow search by contact name
 * Prepare to drop Broadcast.send_all and .urns

v8.1.27 (2023-02-10)
-------------------------
 * Move all form text from Trigger model to forms
 * Add migration to convert URNs to contacts on scheduled broadcasts

v8.1.26 (2023-02-10)
-------------------------
 * Remove returning specific URNs from omniboxes and instead match contacts by URN
 * Rework spa menu eliminate mapping

v8.1.25 (2023-02-09)
-------------------------
 * Remove support for unused v1 omnibox format
 * Update broadcasts API endpoint to support attachments

v8.1.24 (2023-02-08)
-------------------------
 * Update to latest cryptography library
 * Add task to interrupt flow sessions after 90 days

v8.1.23 (2023-02-06)
-------------------------
 * Fix flow results redirecting to it's own page
 * Make sure WA numbers can only be claimed once

v8.1.22 (2023-02-06)
-------------------------
 * Update to latest django to get security fix

v8.1.21 (2023-02-06)
-------------------------
 * Fix export > import path on new ui
 * Fix login redirects from pjax calls

v8.1.20 (2023-02-02)
-------------------------
 * Add servicing menu on org read

v8.1.19 (2023-02-01)
-------------------------
 * Add Msg.quick_replies
 * Add Broadcast.query
 * More generic servicing for staff users

v8.1.18 (2023-02-01)
-------------------------
 * Drop un-used Media.name field

v8.1.17 (2023-01-31)
-------------------------
 * Fix modax from menu bug

v8.1.15 (2023-01-30)
-------------------------
 * Add new org chooser with avatars in new UI
 * Add dashboard to menu in new UI

v8.1.14 (2023-01-27)
-------------------------
 * Add ordering support for filters
 * Fix redirect ping pong when managing orgs
 * Tweak inspect_flows command to report spec veresion mismatches

v8.1.13 (2023-01-26)
-------------------------
 * Update flow editor

v8.1.12 (2023-01-26)
-------------------------
 * Add locale field to Msg

v8.1.11 (2023-01-25)
-------------------------
 * Add migration to alter flow language field to first update any remaining flows with 'base'

v8.1.10 (2023-01-25)
-------------------------
 * Require flow and broadcast base languages to 3 letters
 * Require broadcast.translations to be non-null

v8.1.9 (2023-01-25)
-------------------------
 * Drop unused broadcast fields

v8.1.8 (2023-01-24)
-------------------------
 * Make Broadcast.text nullable and stop writing it

v8.1.7 (2023-01-24)
-------------------------
 * Stop reading from Broadcast.text

v8.1.6 (2023-01-23)
-------------------------
 * Fix campaign imports so we don't import base as a language
 * Increase max-width for channel configuration page
 * Support bandwidth channel type

v8.1.5 (2023-01-23)
-------------------------
 * Data migration to backfill broadcast.translations and replace base with und

v8.1.4 (2023-01-20)
-------------------------
 * Update campaign message events with language base
 * Make servicing to use posterize

v8.1.3 (2023-01-19)
-------------------------
 * Tweak broadcasts API endpoint so it filters by is_active and hits index
 * Fix indexes used for tickets API endpoint
 * Remove unused indexes on contacts_contact
 * Bump engine version to 13.2

v8.1.2 (2023-01-19)
-------------------------
 * Fixes for content menu changes
 * Fix test_db to create orgs with flow languages

v8.1.1 (2023-01-18)
-------------------------
 * Restrict creating surveyor flows unless that is enabled as a feature
 * Always create braodcasts with status = QUEUED, create index for fetching queued broadcasts
 * Add new translations JSON field to broadcasts and start writing it
 * Remove support for creating broadcasts with legacy expressions
 * New content menu component

v8.1.0 (2023-01-17)
-------------------------
 * Update contact import styling
 * Implement squashed migrations
 * Stop trimming flow starts as this will be handled by archiver

v8.0.1 (2023-01-12)
-------------------------
 * Tweak migration dependencies to ensure clean installs run them in order that works
 * Add empty migrations required for squashing

v8.0.0 (2023-01-10)
-------------------------
 * Update deps

v7.5.149 (2023-01-10)
-------------------------
 * Drop FlowRunCount model

v7.5.148 (2023-01-09)
-------------------------
 * Stop squashing FlowRunCount
 * Add misisng index on FlowRunStatusCount and rework get_category_counts to be deterministic
 * Stop creating flows_flowruncount rows in db triggers and remove unsquashed index
 * Bump required pg_dump version for mailroom_db command to 14

v7.5.147 (2023-01-09)
-------------------------
 * Use und (Undetermined) as default flow language and add support for mul (Multiple)
 * Disallow empty and null flow languages, change default spec version to zero
 * Tweak migrate_flows to have smaller batch size and order by org to increase org assets cache hits

v7.5.146 (2023-01-05)
-------------------------
 * Cleanup migrate_flows command and stop excluding flows with version 11.12
 * Change sample flows language to eng
 * Refresh menu when tickets are updated
 * Fix frame-top analytics includes
 * Fix transparency issue with content menu on editor page

v7.5.145 (2023-01-04)
-------------------------
 * Update flow editor to include fix for no expiration route on ivr
 * Stop defaulting to base for new flow languages

v7.5.144 (2023-01-04)
-------------------------
 * Ensure all orgs have at least one flow language
 * Switch to using temba-date in more places

v7.5.143 (2023-01-02)
-------------------------
 * Update mailroom version for CI
 * Tidy up org creation (signups and grants)

v7.5.142 (2022-12-16)
-------------------------
 * Fix org listing when org has no users left

v7.5.141 (2022-12-16)
-------------------------
 * Fix searching for orgs on manage list page
 * Fix highcharts colors
 * Fix invalid template name

v7.5.140 (2022-12-15)
-------------------------
 * Fix flow results page

v7.5.136 (2022-12-15)
-------------------------
 * Tell codecov to ignore static/
 * Switch label action buttons to use temba-dropdown

v7.5.135 (2022-12-13)
-------------------------
 * Fix content menu display issues

v7.5.134 (2022-12-13)
-------------------------
 * Switch to yarn

v7.5.133 (2022-12-12)
-------------------------
 * Bump required python version to 3.10

v7.5.132 (2022-12-12)
-------------------------
 * Support Python 3.10

v7.5.131 (2022-12-09)
-------------------------
 * Replace .gauge on analytics backend with .gauges which allows backends to send guage values in bulk
 * Remove celery auto discovery for jiochat and wechat tasks which were removed

v7.5.130 (2022-12-09)
-------------------------
 * Record cron time in analytics

v7.5.129 (2022-12-08)
-------------------------
 * Cleanup cron task names
 * Split task to trim starts and sessions into two separate tasks
 * Expose all status counts on flows endpoint
 * Read from FlowRunStatusCount instead of FlowRunCount
 * Track flow start counts in statement rather than row level trigger

v7.5.128 (2022-12-07)
-------------------------
 * Record cron task last stats in redis
 * Switch from flake8 to ruff
 * Add data migration to convert exit_type counts to status counts

v7.5.127 (2022-12-07)
-------------------------
 * Fix counts for triggers on the menu

v7.5.126 (2022-12-06)
-------------------------
 * Add new count model for run statuses managed by by-statement db triggers

v7.5.125 (2022-12-05)
-------------------------
 * Tweak index used to find messages to retry so that it includes PENDING messages

v7.5.124 (2022-12-05)
-------------------------
 * Update to latest components
 * More updates for manage pages

v7.5.123 (2022-12-02)
-------------------------
 * Fix bulk labelling flows

v7.5.122 (2022-12-02)
-------------------------
 * Add user read page
 * Latest components
 * Rework notification and incident types to function more like other typed things
 * Add org timezone to manage page
 * Remove no longer used group list view
 * Log celery task completion by default and rework some tasks to return results included in the logging
 * Refresh browser on field deletion in legacy
 * Show org plan end as relative time
 * Don't show location field types as options on deploys where locations aren't enabled

v7.5.121 (2022-11-30)
-------------------------
 * Fix loading of notification types

v7.5.120 (2022-11-30)
-------------------------
 * Rework notification types to work more like channel types
 * Update API fields endpoint to use name and type for writes as well as reads
 * Remove unused field on campaign events write serializer
 * Change undocumented pinned field on fields endpoint to be featured
 * Add usages field to fields API endpoint, as well as name and type to replace label and value_type
 * Add Line error reference URL

v7.5.119 (2022-11-29)
-------------------------
 * Fix flow label in list buttons
 * Fix editor StartSessionForm bug for definitions without exclusions
 * Remove no longer needed check for plan=parent

v7.5.118 (2022-11-28)
-------------------------
 * Add telgram and viber error reference URLs
 * Make Org.plan optional
 * Add support to create new workspaces from org chooser

v7.5.117 (2022-11-23)
-------------------------
 * Update to latest editor
 * Drop Org.is_multi_org and Org.is_multi_user which have been replaced by Org.features

v7.5.116 (2022-11-23)
-------------------------
 * Fix flow label name display

v7.5.115 (2022-11-22)
-------------------------
 * Default to no features on new child orgs
 * Add features field to org update UI

v7.5.114 (2022-11-22)
-------------------------
 * Add Org.features and start writing it
 * Add error ref url for FBA and IG
 * Update temba-components to get new link icon
 * Cleanup msg status constants
 * Always create new orgs with default plan and only show org_plan for non-child orgs

v7.5.113
----------
 * Stop reading Label.label_type and make nullable
 * Remove all support for labels with parents

v7.5.112
----------
 * Remove OrgActivity

v7.5.111
----------
 * Delete associated exports when trying to delete message label folders

v7.5.110
----------
 * Data migration to flatten msg labels

v7.5.109
----------
 * Remove logic for which plan to use for a new org

v7.5.108
----------
 * Tweak how get_new_org_plan is called
 * Move isort config to pyproject
 * Remove no longer used workspace plan

v7.5.107
----------
 * Treat parent and workspace plans as equivalent

v7.5.106
----------
 * Tweak flow label flatten migration to not allow new names to exceed 64 chars

v7.5.105
----------
 * Display channel logs with earliest at top

v7.5.104
----------
 * Remove customized 500 handler
 * Remove sentry support
 * Data migration to flatten flow labels
 * Fix choice of brand for new orgs and move plan selection to classmethod
 * Catch CSV corrupted errors

v7.5.103
----------
 * Some people don't care for icon constants
 * Remove shim for browsers older than IE9
 * Remove google analytics settings

v7.5.102
----------
 * Remove google analytics

v7.5.101
----------
 * Fix Org.promote

v7.5.100
----------
 * Add Org.promote utility method
 * Simplify determining whether to rate limit an API request by looking at request.auth
 * Data migration to simplify org hierarchies

v7.5.99
----------
 * Rename security_settings.py > settings_security.py for consistency
 * Drop Org.uses_topups, TopUp, and Debit
 * Update to latest components
 * Remove unused settings
 * Remove TopUp, Debit and Org.uses_topups

v7.5.98
----------
 * Drop triggers, indexes and functions related to topups

v7.5.97
----------
 * Update mailroom_db command to use postgresql 13
 * Remove User.get_org()
 * Always explicitly provide org when requesting a user API token
 * Remove Msg.topup, TopUpCredits, and CreditAlert
 * Test against latest redis 6.2, elastic 7.17.7 and postgres 13 + 14

v7.5.96
----------
 * Remove topup credits squash task from celery beat

v7.5.95
----------
 * Update API auth classes to set request.org and use that to set X-Temba-Org header
 * Use dropdown for brand field on org update form
 * Remove topups

v7.5.94
----------
 * Add missing migration
 * Remove support for orgs with brand as the host
 * Remove brand tiers

v7.5.93
----------
 * Fix new event modal listeners
 * Re-add org plan and plan end to update form
 * Add png of rapidpro logo
 * Update mailroom_db and test_db commands to set org brand as slug
 * Add data migration to convert org.brand to be the brand slug

v7.5.92
----------
 * Create cla.yml
 * Rework branding to not require modifying what is in the settings

v7.5.91
----------
 * Remove outdated contributor files

v7.5.90
----------
 * Update flow editor
 * Remove unused fields from ChannelType
 * Allow non-beta users to add WeChat channels

v7.5.89
----------
 * Properly truncate the channel name when claiming a WAC channel
 * Fix not saving selected date format to new child org
 * Add redirect from org_create_child if org has a parent
 * Remove unused Org.get_account_value
 * Don't allow creation of child orgs within child orgs
 * Remove low credit checking code

v7.5.88
----------
 * Remove the token refresh tasks for jiochat and wechat channels as courier does this on demand
 * Remove Stripe and bundles functionality

v7.5.87
----------
 * Remove unused segment and intercom dependencies
 * Remove unused utils code
 * Update TableExporter to prepare values so individual tasks don't have to
 * Update versions of mailroom etc that we use for testing
 * Add configurable group membership columns to message, ticket and results exports (WIP)

v7.5.86
----------
 * Remove no-loner used credit alert email templates
 * Drop ChannelConnection

v7.5.85
----------
 * Remove unschedule option from scheduled broadcast read page
 * Only show workspace children on settings menu
 * Allow adding Android channel when its number is used on a WhatsApp channel
 * Remove credit alert functionality
 * Add scheduled message delete modal

v7.5.84
----------
 * No link fields on sub org page

v7.5.83
----------
 * Update telegram library which doesn't work with Python 3.10
 * Add user child workspace management
 * Remove topup management views

v7.5.82
----------
 * Add JustCall channel type

v7.5.81
----------
 * Always show plan formax even for orgs on topups plan

v7.5.80
----------
 * Remove task to suspend topups orgs

v7.5.79
----------
 * Add new indexes for scheduled broadcasts view and API endpoint
 * Update broadcast_on_change db trigger to check is_active
 * Use database trigger to prevent status changes on flow sessions that go from exited to waiting

v7.5.78
----------
 * Remove old crisp templates
 * Added Broadcast.is_active backfill migration

v7.5.77
----------
 * Proper redirect when removing channels
 * Fix api header when logged out
 * Take features out of branding and make it deployment level and remove api_link
 * Get rid of flow_types as a branding setting

v7.5.76
----------
 * Tweak migration to convert missed call triggers to ignore archived triggers

v7.5.75
----------
 * Add Broadcast.is_active and set null=true and default=true
 * Remove channel_status_processor context processor
 * Add data migration to delete or convert missed call triggers

v7.5.74
----------
 * Fix webhook list page to not show every call as an error
 * Small styling tweaks for api docs
 * Remove fields from msgs event payloads that are no longer used

v7.5.73
----------
 * Update api docs to be nav agnostic  
 * Rewrite API Explorer to be vanilla javascript
 * Use single permissions for all msg and contact list views
 * Rework UI for incoming call triggers to allow selecting non-voice flows
 * Remove send action from messages, add download results for flows
 * Unload flow editor when navigating away

v7.5.72
----------
 * Always put service menu options at end of menu in new group

v7.5.71
----------
 * More appropriate login page, remove legacy textit code

v7.5.70
----------
 * Fix which fields should be on org update modal
 * Honor brand config for signup

v7.5.69
----------
 * Fix race on editor load

v7.5.68
----------
 * Add failed reason for channel removed
 * Remove no longer used channels option from interrupt_sessions task

v7.5.67
----------
 * Interrupt channel by mailroom task

v7.5.66
----------
 * Remove need for jquery on spa in-page loads
 * Remove key/secret hardcoding for boto session

v7.5.65
----------
 * Queue relayer messages with channel UUID and id
 * No nouns for current object in menus except for New
 * Add common contact field inclusion to exports
 * Fix new scheduled message menu option
 * Fix releasing other archive files to use proper pagination

v7.5.64
----------
 * Add an unlinked call list page
 * Show channel log links on more pages to more users

v7.5.63
----------
 * Fix handling of relayer messages
 * Add missing email templates for ticket exports

v7.5.62
----------
 * Add attachment_fetch as new channel log type

v7.5.61
----------
 * Fix claiming vonage channels for voice
 * Better approach for page titles from the menu
 * Fix layout for ticket menu in new ui

v7.5.60
----------
 * Fix the flow results export modal

v7.5.59
----------
 * Delete attachments from storage when deleting messages
 * Add base export class for exports with contact data
 * Actually make date range required for message exports (currently just required in UI))
 * Add date range filtering to ticket and results exports
 * Add ticket export (only in new UI for now)

v7.5.58
----------
 * Add twilio and vonage connection formax entries in new UI
 * Update both main menu and content menus to align with new conventions
 * Gate new UI by Beta group rather than staff
 * Don't show new menu UIs until they're defined

v7.5.57
----------
 * Move status updates into update contact view
 * Some teaks to rendering of channel logs
 * Cleanup use of channelconnection in preparation for dropping

v7.5.56
----------
 * Really really fix connection migration

v7.5.55
----------
 * Really fix connection migration

v7.5.54
----------
 * Fix migration to convert connections to calls

v7.5.53
----------
 * Add data migration to convert channel connections to calls

v7.5.52
----------
 * Replace last non-API usages of User.get_org()
 * Use new call model in UI

v7.5.51
----------
 * Add new ivr.Call model to replace channels.ChannelConnection

v7.5.50
----------
 * Drop no-longer used ChannelLog fields
 * Drop Msg.logs (replaced by .log_uuids)
 * Drop ChannelConnection.connection_type

v7.5.49
----------
 * Fix test failing because python version changed
 * Allow background flows for missed call triggers
 * Different show url for spa and non-spa tickets
 * Update editor to include fix for localizing categories for some splits
 * Add data migration to delete existing missed call triggers for non-message flows
 * Restrict Missed Call triggers to messaging flows

v7.5.48
----------
 * Stop recommending Android, always recommend Telegram
 * Drop IVRCall proxy model and use ChannelConnection consistently
 * Add migration to delete non-IVR channel connections
 * Fix bug in user releasing and remove special superuser handling in favor of uniform treatment of staff users

v7.5.47
----------
 * Switch to temba-datepicker

v7.5.46
----------
 * Fix new UI messages menu

v7.5.45
----------
 * Replace some occurences of User.get_org()
 * Add new create modal for scheduled broadcasts

v7.5.44
----------
 * Add data migration to cleanup counts for SystemLabel=Calls
 * Tweak ordering of Msg menu sections
 * Add slack channel

v7.5.43
----------
 * Include config for mailroom test db channels
 * Remove Calls from msgs section
 * Update wording of Missed Call triggers to clarify they should only be used with Android channels
 * Only show Missed Call trigger as option for workspaces with an Android channel
 * Change ChannelType.is_available_to and is_recommended_to to include org

v7.5.42
----------
 * Add data migration to delete legacy channel logs
 * Drop support for channel logs in legacy format

v7.5.41
----------
 * Fix temba-store

v7.5.40
----------
 * Tweak forgot password success message

v7.5.39
----------
 * Add log_uuids field to ChannelConnection, ChannelEvent and Msg
 * Improve `trim_http_logs_task` performance by splitting the query

v7.5.38
----------
 * Add codecov token to ci.yml
 * Remove unnecessary maxdiff set in tests
 * Fix to allow displaying logs that timed out
 * Add HttpLog util and use to save channel logs in new format
 * Add UUID to channel log and msgs

v7.5.37
----------
 * Show servicing org

v7.5.36
----------
 * Clean up chooser a smidge

v7.5.35
----------
 * Add org-chooser
 * Refresh channel logs
 * Add channel uuid to call log url
 * Fix history state on tickets and contacts  
 * Update footer  
 * Add download icons for archives  
 * Fix create flow modal opener  
 * Flow editor embed styling
 * Updating copyright dates and TextIt name (dba of Nyaruka)

v7.5.34
----------
 * Use elapsed_ms rather than request_time on channel log templates
 * Update components (custom widths for temba-dialog, use anon_display where possible)
 * Switch to temba-dialog based attachment viewer, remove previous libs
 * Nicer collapsing on flow list columns
 * Add overview charts for run results

v7.5.33
----------
 * ChannelLogCRUDL.List should use get_description so that it works if log_type is set
 * Tweak channel log types to match what courier now creates
 * Check for tabs after timeouts, don't auto-collapse flows
 * Add charts to analytics tab

v7.5.32
----------
 * Update components with label fix

v7.5.31
----------
 * Add flow results in new UI

v7.5.30
----------
 * Remove steps for add WAC credit line to businesses

v7.5.29
----------
 * Fix servicing of channel logs

v7.5.28
----------
 * Stop writing to unused media name field
 * Add missing C Msg failed reason
 * Add anon-display field to API contact results if org is anon and make urn display null

v7.5.27
----------
 * Revert change to Contact.Bulk_urn_cache_initialize to have it set org on contacts

v7.5.26
----------
 * Don't set org on bulk initialized contacts

v7.5.25
----------
 * Fix filtering on channel log call page
 * Add anon_display and use that when org is anon instead of using urn_display for anon id
 * Add urn_display to contact reference on serialized runs in API

v7.5.24
----------
 * Fix missing service end button

v7.5.23
----------
 * Update to latest floweditor
 * Add new ChannelLog log type choices and make description nullable
 * Fix more content menus so that they can be fetched as JSON and add more tests

v7.5.22
----------
 * Remove unused policies.policy_read perm
 * Replace all permission checking against Customer Support group with is_staff check on user

v7.5.21
----------
 * Allow views with ContentMenuMixin to be fetched as JSON menu items using a header
 * Add new fields to channel log model and start reading from them if they're set

v7.5.20
----------
 * Update the links for line developers console on the line claim page
 * Rework channel log details views into one generic one, one for messages, one for calls

v7.5.19
----------
 * Rework channel log rendering to use common HTTPLog template
 * Fix titles on channel, classifier and manage logins pages

v7.5.18
----------
 * Workspace and user management in new UI

v7.5.17
----------
 * Show send history of scheduled broadcasts in correct order
 * Only show option to delete runs to users who have that perm, and give editors that perm
 * Update deps

v7.5.16
----------
 * Fixed zaper page title
 * Validate channel name is not more than 64 characters
 * Added 'authentication' to the temba anchor URL text

v7.5.15
----------
 * Fix URL for media uploads which was previously conflicting with media directory

v7.5.14
----------
 * Deprecate Media.name which can always be inferred from .path
 * Improve cleaning of media filenames
 * Convert legacy UUID fields on exports and labels
 * Request instagram_basic permission for IG channels

v7.5.11
----------
 * Don't allow creating of labels with parents or editing labels to have a parent
 * Rework the undocumented media API endpoint to be more specific to surveyor attachments
 * Add MediaCRUDL with upload and list endpoints
 * Remove requiring instagram_basic permission

v7.5.10
----------
 * Remove Media.is_ready, fix setting .status on alternates, add limit for upload size
 * Rework ContentMenuMixin to put the menu in the context, and include new and legacy formats

v7.5.9
----------
 * Add status field to Media, move primary index to UUID field

v7.5.8
----------
 * Update floweditor
 * Convert all views to use ContentMenuMixin instead of get_gear_links
 * Add decorator to mock uuid generation in tests
 * Process media uploads with ffmpeg in celery task

v7.5.7
----------
 * Add constraint to ensure non-waiting/active runs have exited_on set
 * Add constraint to ensure non-waiting sessions have an ended_on

v7.5.6
----------
 * Remove unused upload_recording endpoint
 * Add Media model

v7.5.5
----------
 * Remaining fallback modax references
 * Add util for easier gear menu creation
 * Add option to interrupt a contact from read page

v7.5.4
----------
 * Fix scripts on contact page start modal
 * Add logging for IG channel claim failures
 * Add features to BRANDING which determines whether brands have access to features
 * Sort permissions a-z
 * Fix related names on Flow.topics and Flow.users and add Topic.release
 * Expose opened_by and opened_in over ticket API

v7.5.3
----------
 * Fix id for custom fields modal

v7.5.2
----------
 * Fix typo on archive button
 * Only show active ticketers and topics on Open Ticket modal
 * Add data migration to fix non-waiting sessions with no ended_on

v7.5.1
----------
 * Allow claiming WAC test numbers
 * Move black setting into pyproject.toml
 * Add Open Ticket modal view to contact read page

v7.5.0
----------
 * Improve user list page
 * Add new fields to Ticket record who or what flow opened a ticket
 * Refresh menu on modax redircts, omit excess listeners from legacy lists
 * Fix field label vs name in new UI
 * Add start flow bulk action in new UI
 * Show zeros in menu items in new UI
 * Add workspace selection to account page in new UI
 * Scroll main content pane up on page replacement in new UI

v7.4.2
----------
 * Update copyright notice
 * Update stable versions

v7.4.1
----------
 * Update locale files

v7.4.0
----------
 * Remove superfulous Beta group perm
 * Update new UI opt in permissions
 * More tweaks to WhatsApp Cloud channel claiming

v7.3.79
----------
 * Add missing Facebook ID

v7.3.78
----------
 * Add button to allow admin to choose more FB WAC numbers

v7.3.77
----------
 * Add contact ticket list in new UI
 * Fix permissions to connect WAC
 * Register the WAC number in the activate method

v7.3.76
----------
 * Add the Facebook dialog login if the token is not submitted successfully on WAC org connect
 * Fix campaigns archive and activate buttons
 * Update to latest Django
 * Only display WA templates that are active
 * Update flow start dialog to use start preview endpoint  
 * Add start flow bulk action for contacts

v7.3.75
----------
 * Redirect to channel page after WAC claim
 * Fix org update pre form users roles list
 * Adjust permission for org whatsapp connect view
 * Ignore new conversation triggers without channels in imports

v7.3.74
----------
 * Use FB JS SDK for WAC signups

v7.3.73
----------
 * Add DB constraint to disallow active or waiting runs without a session

v7.3.72
----------
 * Add DB constraint to enforce that flow sessions always have output or output_url

v7.3.71
----------
 * Make sure all limits are updatable on the workspace update view
 * Remove duplicated pagination
 * Enforce channels limit per workspace

v7.3.70
----------
 * Fix workspace group limit check for existing group import
 * Drop no longer used role m2ms

v7.3.69
----------
 * Fix campaign links

v7.3.68
----------
 * Add WhatsApp API version choice field
 * Stop writing to the role specific m2m tables
 * Add pending events tab to contact details

v7.3.67
----------
 * Merge pull request #3865 from nyaruka/plivo_claim
 * formatting
 * Sanitize plivo app names to match new rules

v7.3.66
----------
 * Merge pull request #3864 from nyaruka/fix-WA-templates
 * Fix message templates syncing for new categories

v7.3.65
----------
 * Fix surveyor joins so new users are added to orgmembership as well.

v7.3.64
----------
 * Fix fetching org users with given roles

v7.3.63
----------
 * Update mailroom_db command to correctly add users to orgs
 * Stop reading from org role m2m tables

v7.3.62
----------
 * Fix rendering of dates on upcoming events list
 * Data migration to backfill OrgMembership

v7.3.61
----------
 * Add missing migration

v7.3.60
----------
 * Data migration to fail active/waiting runs with no session
 * Include scheduled triggers in upcoming contact events
 * Add OrgMembership model

v7.3.59
----------
 * Spreadsheet layout for contact fields in new UI
 * Adjust WAC channel claim to add system admin with user token

v7.3.58
----------
 * Clean up chat media treatment
 * Add endpoint to get upcoming scheduled events for a contact
 * Remove filtering by ticketer on tickets API endpoint and add indexes
 * Add status to contacts API endpoint

v7.3.57
----------
 * Improve WAC phone number verification flow and feedback
 * Adjust name of WAC channels to include the number
 * Fix manage user update URL on org update page
 * Support missing target_ids key in WAC responses

v7.3.56
----------
 * Fix deletion of users
 * Cleanup user update form
 * Fix missing users manage link page
 * Add views to verify and register a WAC number

v7.3.55
----------
 * Update contact search summary encoding

v7.3.54
----------
 * Make channel type a property and use to determine redact values in HTTP request logs

v7.3.53
----------
 * Make WAC channel visible to beta group

v7.3.52
----------
 * Fix field name for submitted token

v7.3.51
----------
 * Use default API throttle rates for unauthenticated users
 * Bump pyjwt from 2.3.0 to 2.4.0
 * Cache user role on org
 * Add WhatsApp Cloud channel type

v7.3.50
----------
 * Make Twitter channels beta only for now
 * Use cached role permissions for permission checking and fix incorrect permissions on some 
API views
 * Move remaining mockey patched methods on auth.User to orgs.User

v7.3.49
----------
 * Timings in export stats spreadsheet should be rounded to nearest second
 * Include failed_reason/failed_reason_display on msg_created events
 * Move more monkey patching on auth.User to orgs.User

v7.3.48
----------
 * Include first reply timings in ticket stats export
 * Create a proxy model for User and start moving some of the monkey patching to proper methods on that

v7.3.47
----------
 * Data migration to backfill ticket first reply timings

v7.3.46
----------
 * Add new squashable model to track average ticket reply times and close times
 * Add Ticket.replied_on

v7.3.45
----------
 * Add endpoint to export Excel sheet of ticket daily counts for last 90 days

v7.3.44
----------
 * Remove omnibox support for fetching by label and message
 * Remove functionality for creating new label folders and creating labels with folders

v7.3.43
----------
 * Fix generating cloned flow names so they can't end with trailing spaces
 * Deleting of globals should be soft like other types
 * Simplify checking of workspace limits in UI and API

v7.3.42
----------
 * Data migration to backfill ticket daily counts

v7.3.41
----------
 * Reorganization of temba.utils.models
 * Update the approach to the test a token is valid for FBA and IG channels
 * Promote ContactField and Global to be TembaModels whilst for now retaining their custom name validation logic
 * Add import support methods to TembaModel and use with Topic

v7.3.40
----------
 * Add workspace plan, disallow grandchild org creation.
 * Add support for shared usage tracking

v7.3.39
----------
 * Move temba.utils.models to its own package
 * Queue broadcasts to mailroom with their created_by
 * Add teams to mailroom test database
 * Add is_system to TembaModel, downgrade Contact to SmartModel

v7.3.38
----------
 * Make sure we request a FB long lived page token using a long lived user token
 * Convert campaign and campaignevent to use real UUIDs, simplify use of constants in API

v7.3.37
----------
 * Don't forget to squash TicketDailyCount
 * Fix imports of flows with ticket topic dependencies

v7.3.36
----------
 * Add migration to update names of deleted labels and add constraint to enforce uniqueness
 * Move org limit checking from serializers to API views
 * Generalize preventing deletion of system objects via the API and allow deleting of groups that are used in flows
 * Serialized topics in the API should include system field
 * Add name uniqueness constraints to Team and Topic
 * Add Team and TicketDailyCount models

v7.3.35
----------
 * Tweaks to Topic model to enforce name uniqueness
 * Add __str__ and __repr__ to TembaModel to replace custom methods and remove several unused ones
 * Convert FlowLabel to be a TembaModel

v7.3.34
----------
 * Fix copying flows to generate a unique name
 * Rework TembaModel to be a base model class with UUID and name

v7.3.33
----------
 * Use model mixin for common name functionality across models

v7.3.32
----------
 * Add DB constraint to enforce flow name uniqueness

v7.3.31
----------
 * Update components with resolved locked file

v7.3.29
----------
 * Fix for flatpickr issue breaking date picker
 * ContactField.get_or_create should enforce name uniqeuness and ignore invalid names
 * Add validation error when changing type of field used by campaign events

v7.3.28
----------
 * Tweak flow name uniqueness migration to honor max flow name length

v7.3.27
----------
 * Tweak header to be uniform treatment regardless of menu
 * Data migration to make flow names unique
 * Add flow.preview_start endpoint which calls mailroom endpoint

v7.3.26
----------
 * Fix mailroom_db command to set languages on new orgs
 * Fix inline menus when they have no children
 * Fix message exports

v7.3.25
----------
 * Fix modals on spa pages
 * Add service button to org edit page
 * Update to latest django
 * Add flow name to message Export if we have it

v7.3.24
----------
 * Allow creating channel with same address when schemes do not overlap

v7.3.23
----------
 * Add status to list of reserved field keys
 * Migration to drop ContactField.label and field_type

v7.3.22
----------
 * Update contact modified_on when deleting a group they belong to
 * Add custom name validator and use for groups and flows

v7.3.21
----------
 * Fix rendering of field names on contact read page
 * Stop writing ContactField.label and field_type

v7.3.20
----------
 * Stop reading ContactField.label and field_type

v7.3.19
----------
 * Correct set new ContactField fields in mailroom_db test_db commands
 * Update version of codecov action as well as versions of rp-indexer and mailroom used by tests
 * Data migration to populate name and is_system on ContactField

v7.3.18
----------
 * Give contact fields a name and is_system db field
 * Update list of reserved keys for contact fields

v7.3.17
----------
 * Fix uploading attachments to properly get uploaded URL

v7.3.16
----------
 * Fix generating of unique flow, group and campaign names to respect case-insensitivity and max name length
 * Add data migration to prefix names of previously deleted flows
 * Prefix flow names with a UUID when deleted so they don't conflict with other flow names
 * Remove warning about feature on flow start modal being removed

v7.3.15
----------
 * Check name uniqueness on flow creation and updating
 * Cleanup existing field validation on flow and group forms
 * Do not fail to release a channel when we cannot reach the Facebook API for FB channels

v7.3.14
----------
 * Convert flows to be a soft dependency

v7.3.13
----------
 * Replace default index on FlowRun.contact with one that includes flow_id

v7.3.12
----------
 * Data migration to give every workspace an Open Tickets smart system group

v7.3.11
----------
 * Fix bulk adding/removing to groups from contact list pages
 * Convert groups into a soft dependency for flows
 * Use dataclasses instead of NaamedTuples where appropriate

v7.3.10
----------
 * Remove path from example result in runs API endpoint docs
 * Prevent updating or deleting of system groups via the API or UI
 * Add system property to groups endpoint and fix docs

v7.3.9
----------
 * Remove IG channel beta gating

v7.3.8
----------
 * Fix fetching of groups from API when using separate readonly DB connection

v7.3.7
----------
 * Rework how we fetch contact groups

v7.3.6
----------
 * For FB / IG claim pages use expiring token if no long lived token is provided

v7.3.5
----------
 * Data migration to update group_type=U to M|Q

v7.3.4
----------
 * Merge pull request #3734 from nyaruka/FB-IG-claim

v7.3.3
----------
 * Check all org groups when creating unique group names
 * Make ContactGroup.is_system non-null and switch to using to distinguish between system and user groups

v7.3.2
----------
 * Data migration to populate ContactGroup.is_system

v7.3.1
----------
 * Add is_system field to ContactGroup and rename 'dynamic' to 'smart'
 * Return 404 from edit_sub_org if org doesn't exist
 * Use live JS SDK for FBA and IG refresh token views
 * Add scheme to flow results exports

v7.3.0
----------
 * Add countries supported by Africastalking
 * Replace empty squashed migrations with real ones

v7.2.4
----------
 * Update stable versions in README

v7.2.3
----------
 * Add empty versions of squashed migrations to be implemented in 7.3

v7.2.2
----------
 * Updated translations from Transifex
 * Fix searching on calls list page

v7.2.1
----------
 * Update locale files

v7.2.0
----------
 * Disallow PO export/import for archived flows because mailroom doesn't know about them
 * Add campaigns section to new UI

v7.1.82
----------
 * Update to latest flake8, black and isort

v7.1.81
----------
 * Remove unused collect_metrics_task
 * Bump dependencies

v7.1.80
----------
 * Remove progress bar on facebook claim
 * Replace old indexes based on flows_flowrun.is_active

v7.1.79
----------
 * Remove progress dots for FBA and IG channel claim pages
 * Actually drop exit_type, is_active and delete_reason on FlowRun
 * Fix group name validation to include system groups

v7.1.78
----------
 * Test with latest indexer and mailroom
 * Stop using FlowRun.exit_type, is_active and delete_reason

v7.1.77
----------
 * Tweak migration as Postgres won't let us drop function being used

v7.1.76
----------
 * Update vonage deprecated methods

v7.1.75
----------
 * Rework flowrun db triggers to use status rather than exit_type or is_active

v7.1.74
----------
 * Allow archiving of flow messages
 * Don't try interrupting session that is about to be deleted
 * Tweak criteria for who can preview new interface

v7.1.73
----------
 * Data migration to fix facebook contacts name

v7.1.72
----------
 * Revert database trigger changes which stopped deleting path and exit_type counts on flowrun deletion

v7.1.71
----------
 * Fix race condition in contact deletion
 * Rework flowrun database triggers to look at delete_from_results instead of delete_reason

v7.1.69
----------
 * Update to latest floweditor

v7.1.68
----------
 * Add FlowRun.delete_from_results to replace delete_reason

v7.1.67
----------
 * Drop no longer used Msg.delete_reason and delete_from_counts columns
 * Update to Facebook Graph API v12

v7.1.66
----------
 * Fix last reference to Msg.delete_reason in db triggers and stop writing that on deletion

v7.1.65
----------
 * Rework msgs database triggers so we don't track counts for messages in archives

v7.1.64
----------
 * API rate limits should be org scoped except for staff accounts
 * Expose current flow on contact read page for all users
 * Add deprecation text for restart_participants

v7.1.63
----------
 * Fix documentation of contacts API endpoint
 * Release URN channel events in data migration to fix deleted contacts with tickets
 * Use original filename inside UUID folder to upload media files

v7.1.62
----------
 * Tweak migration to only fully delete inactive contacts with tickets

v7.1.61
----------
 * Add flow field to contacts API endpoint
 * Add support to the audit_es command for dumping ES queries
 * Add migration to make sure contacts which we failed to delete are really deleted
 * Fix contact release with tickets having a broadcast

v7.1.60
----------
 * Adjust WA message template warning to not be show for Twilio WhatsApp channels
 * Add support to increase API rates per org

v7.1.59
----------
 * Add migration to populate Contact.current_flow

v7.1.58
----------
 * Restrict msg visibility changes on bulk actions endpoint

v7.1.57
----------
 * Add sentry id for 500 page
 * Display current flow on contact read page for beta users
 * Add new msg visibility for msgs deleted by senders and allow deleted msgs to appear redacted in contact histories
 * Contact imports should strip empty rows, missing a UUID or URNs

v7.1.56
----------
 * Fix issue with sending to step_node
 * Add missing languages for whatsapp templates
 * Add migration to remove inactive contacts from user groups

v7.1.55
----------
 * Fix horizontal scrolling in editor
 * Add support to undo_footgun command to revert status changes

v7.1.53
----------
 * Relayer syncing should ignore bad URNs that fail validation in mailroom
 * Add unique constraint to ContactGroup to enforce name uniqueness within an org

v7.1.52
----------
 * Fix scrolling select

v7.1.51
----------
 * Merge pull request #3671 from nyaruka/ui-widget-fixes
 * Fix select for slow clicks and removing rules in the editor

v7.1.50
----------
 * Add migration to make contact group names unique within an organization
 * Add cookie based path to opt in and out of new interface

v7.1.49
----------
 * Update to Django 4

v7.1.48
----------
 * Make IG channel beta gated
 * Remove expires_on, parent_uuid and connection_id fields from FlowRun
 * Add background flow options to campaign event dialog

v7.1.47
----------
 * Make FlowSession.wait_resume_on_expire not-null

v7.1.46
----------
 * Add migration to set wait_resume_on_expire on flow sessions
 * Update task used to update run expirations to also update them on the session

v7.1.45
----------
 * Make FlowSession.status non-null and add constraint to ensure waiting sessions have wait_started_on and wait_expires_on set

v7.1.44
----------
 * Fix login via password managers
 * Change gujarati code language to 'guj'
 * Add instagram channel type
 * Add interstitial when inactive contact search meets threshold

v7.1.42
----------
 * Add missing migration

v7.1.41
----------
 * Add Contact.current_flow

v7.1.40
----------
 * Drop FlowRun.events and FlowPathRecentRun

v7.1.39
----------
 * Include qrious.js script
 * Add FlowSession.wait_resume_on_expire
 * Add Msg.flow

v7.1.38
----------
 * Replace uses of deprecated Django functions
 * Remove crisp and librato analytics backends and add ConsoleBackend as example
 * Data migration to populate FlowSession.wait_started_on and wait_expires_on

v7.1.37
----------
 * Migration to remove recent run creation from db triggers
 * Remove no longer used recent messages view and functionality on FlowPathRecentRun

v7.1.36
----------
 * Add scheme column on contact exports for anon orgs
 * Remove option to include router arguments in downloaded PO files
 * Make loading of analytics backends dynamic based on setting of backend class paths

v7.1.35
----------
 * Only display crisp support widget if brand supports it
 * Do crisp chat widget embedding via analytics template hook

v7.1.34
----------
 * Update to editor v1.16.1

v7.1.33
----------
 * Add management to fix broken flows
 * Use new recent contacts endpoint for editor

v7.1.32
----------
 * Temporarily put crisp_website_id back in context

v7.1.31
----------
 * Remove include_msgs option of flow result exports

v7.1.30
----------
 * Update to latest flow editor

v7.1.29
----------
 * Update to latest floweditor
 * Add FlowSession.wait_expires_on
 * Improve validation of flow expires values
 * Remove segment and intercom integrations and rework librato and crisp into a pluggable analytics framwork

v7.1.28
----------
 * Convert FlowRun.id and FlowSession.id to BIGINT

v7.1.27
----------
 * Drop no longer used FlowRun.parent

v7.1.26
----------
 * Prefer UTF-8 if we're not sure about encoding of CSV import

v7.1.25
----------
 * Fix Kaleyra claim blurb
 * Fix HTTPLog read page showing warning shading for healthy calls

v7.1.24
----------
 * Fix crisp identify on signup
 * Use same event structure for Crisp as others

v7.1.23
----------
 * Update help links for the editor
 * Add failed reason for failed destination such as missing channel or URNs
 * Add view to fetch recent contacts from Redis

v7.1.22
----------
 * Fix join syntax

v7.1.21
----------
 * Fix join syntax, argh

v7.1.20
----------
 * Arrays not allowed on track events

v7.1.19
----------
 * Add missing env to settings_common

v7.1.18
----------
 * Implement crisp as an analytics integration

v7.1.17
----------
 * Tweak event tracking for results exports
 * Revert change to hide non-responded runs in UI

v7.1.16
----------
 * Drop Msg.response_to
 * Drop Msg.connection_id

v7.1.15
----------
 * Remove path field from API runs endpoint docs
 * Hide options to include non-responded runs on results download modal and results page
 * Fix welcome page widths
 * Update mailroom_db to require pg_dump version 12.*
 * Update temba-components
 * Add workspace page to new UI

v7.1.14
----------
 * Fix wrap for recipients list on flow start log
 * Set Msg.delete_from_counts when releasing a msg
 * Msg.fail_old_messages should set failed_reason
 * Add new fields to Msg: delete_from_counts, failed_reason, response_to_external_id
 * Tweak msg_dewire command to only fetch messages which have never errored

v7.1.13
----------
 * Add management command to dewire messages based on a file of ids
 * Render webhook calls which are too slow as errors

v7.1.12
----------
 * Remove last of msg sending code
 * Fix link to webhook log

v7.1.11
----------
 * Remove unnecessary conditional load of jquery

v7.1.10
----------
 * Make forgot password email look a little nicer and be easier to localize

v7.1.9
----------
 * Fix email template for password forgets

v7.1.8
----------
 * Remove chatbase as an integration as it no longer exists
 * Clear keyword triggers when switching to flow type that doesn't support them
 * Use branded emails for export notifications

v7.1.5
----------
 * Remove warning on flow start modal about settings changes
 * Add privacy policy link
 * Test with Redis 3.2.4
 * Updates for label sub menu and internal menu navigation

v7.1.4
----------
 * Remove task to retry errored messages which now handled in mailroom

v7.1.2
----------
 * Update poetry dependencies
 * Update to latest editor

v7.1.1
----------
 * Remove channel alert notifications as these will become incidents
 * Add Incident model as well as OrgFlagged and WebhooksUnhealthy types

v7.1.0
----------
 * Drop no longer used index on msg UUID
 * Re-run collect_sql
 * Use std collection types for typing hints and drop use of object in classes

v7.0.4
----------
 * Fix contact stop list page 
 * Update to latest black to fix errors on Python 3.9.8
 * Add missing migration

v7.0.3
----------
 * Update to latest editor v1.15.1
 * Update locale files which adds cs and mn

v7.0.2
----------
 * Update editor to v1.15 with validation fixes
 * Fix outbox pagination
 * Add generic title bar with new dropdown on spa

v7.0.1
----------
 * Add missing JS function to delete messages in the archived folder
 * Update locale files

v7.0.0
----------
 * Fix test failing to due bad domain lookup

v6.5.71
----------
 * Add migration to remove deleted contacts and groups from scheduled broadcasts
 * Releasing a contact or group should also remove it from scheduled broadcasts

v6.5.70
----------
 * Fix intermittent credit test failure
 * Tidy up Msg and Broadcast constants
 * Simplify settings for org limit defaults
 * Fix rendering of deleted contacts and groups in recipient lists

v6.5.69
----------
 * Remove extra labels on contact fields

v6.5.68
----------
 * Reenable chat monitoring

v6.5.67
----------
 * Make ticket views and components in sync

v6.5.66
----------
 * Add channel menu
 * Add test for dynamic contact group list, remove editor_next redirect
 * Fix styling on contact list headersa and flow embedding
 * Add messages to menu, refresh override
 * Switch contact fields and import to use template inheritance
 * Use template inheritance for spa work
 * Add deeplinking support for non-menued destinations

v6.5.65
----------
 * Move to Python 3.9

v6.5.64
----------
 * Fix export notification email links

v6.5.63
----------
 * When a contact is released their tickets should be deleted
 * Test on PG 12 and 13
 * Use S3 Select for message exports
 * Use new notifications system for export emails

v6.5.62
----------
 * Use crontab for WA tokens task schedule
 * Allow keyword triggers to be single emojis
 * Celery 5.x

v6.5.60
----------
 * Add option to audit_archives to check flow run counts
 * Drop no longer used ticket subject column
 * Add contact read page based on contact chat component

v6.5.59
----------
 * Less progress updates in audit_archives
 * Tweak tickets API endpoint to accept a uuid URL param

v6.5.58
----------
 * Add progress feedback to audit_archives
 * Update locale files

v6.5.57
----------
 * Fix Archive.rewrite

v6.5.56
----------
 * Encode content hashes sent to S3 using Base64

v6.5.55
----------
 * Trim mailgun ticketer names to <= 64 chars when creating
 * Management command to audit archives
 * Use field limiting on omnibox searches

v6.5.54
----------
 * Fix S3 select query generation for date fields

v6.5.53
----------
 * Disable all sentry transactions
 * Use S3 select for flow result exports
 * Add utils for compiling S3 select queries

v6.5.52
----------
 * Merge pull request #3555 from nyaruka/ticket-att
 * Update test to include attachment list for last_msg
 * Update CHANGELOG.md for v6.5.51
 * Merge pull request #3553 from nyaruka/httplog_tweaks
 * Merge pull request #3554 from nyaruka/s3_retries
 * Add other missing migration
 * Add retry config to S3 client
 * Add missing migration to drop WebhookResult model
 * Update CHANGELOG.md for v6.5.50
 * Merge pull request #3552 from nyaruka/fix-WA-check-health-logs
 * Fix tests
 * Add zero defaults to HTTPLog fields, drop WebHookResult and tweak HTTPLog templates for consistency
 * Fix response for WA message template to be HTTP response
 * Update CHANGELOG.md for v6.5.49
 * Merge pull request #3549 from nyaruka/retention_periods
 * Merge pull request #3546 from nyaruka/readonly_exports
 * Merge pull request #3548 from nyaruka/fix-WA-check-health-logs
 * Merge pull request #3550 from nyaruka/truncate-org
 * Use single retention period setting for all channel logs
 * Truncate org name with ellipsis on org chooser
 * Add new setting for retention periods for different types and make trimming tasks more consistent
 * Use readonly database connection for contact, message and results exports
 * Add migration file
 * Log update WA status error using HTTPLog

v6.5.51
----------
 * Add retry config to S3 client
 * Add zero defaults to HTTPLog fields, drop WebHookResult and tweak HTTPLog templates for consistency

v6.5.50
----------
 * Fix response for WA message template to be HTTP response

v6.5.49
----------
 * Truncate org name with ellipsis on org chooser
 * Add new setting for retention periods for different types and make trimming tasks more consistent
 * Use readonly database connection for contact, message and results exports
 * Log update WA status error using HTTPLog

v6.5.48
----------
 * Fix clear contact field event on ticket history

v6.5.47
----------
 * Use readonly database connection for contacts API endpoint
 * Use webhook_called events from sessions for contact history
 * Remove unused webhook result views and improve httplog read view
 * Fix API endpoints not always using readonly database connection and add testing

v6.5.46
----------
 * Move list refresh registration out of content block

v6.5.45
----------
 * Temporarily disable refresh
 * Don't use readonly database connection for GETs to contacts endpoint
 * Add view for webhook calls saved as HTTP logs
 * Pass location support flag to editor as a feature flag

v6.5.44
----------
 * GET requests to API should use readonly database on the view's queryset

v6.5.43
----------
 * Tweak how HTTP logs are deleted
 * Add num_retries field to HTTPLog

v6.5.42
----------
 * Pin pyopenxel to 3.0.7 until 3.0.8 release problems resolved
 * Add new fields to HTTPLog to support saving webhook results
 * Make TPS for Shaqodoon be 5 by default
 * Make location support optional via new branding setting

v6.5.41
----------
 * Update editor with fix for field creation
 * Minor tidying of HTTPLog
 * Fix rendering of tickets on contact read page which now don't have subjects

v6.5.40
----------
 * Update to floweditor 1.14.2
 * Tweak database settings to add new readonly connection and remove no longer used direct connection
 * Update menu on ticket list update

v6.5.38
----------
 * Deprecate subjects on tickets in favor of topics
 * Tweak ticket bulk action endpoint to allow unassigning
 * Add API endpoint to read and write ticket topics

v6.5.37
----------
 * Add tracking of unseen notification counts for users
 * Clear ticket notifications when visiting appropriate ticket views
 * Remove no longer used Log model

v6.5.36
----------
 * Revert cryptography update

v6.5.35
----------
 * Update to newer pycountry and bump other minor versions
 * Fix ticketer HTTP logs not being accessible
 * Add management command to re-eval a smart group
 * Add comment to event_fires about mailroom issue
 * Fix indexes on tickets to match new UI
 * Now that mailroom is setting ContactImport.status, use in reads

v6.5.34
----------
 * Update to latest components (fixes overzealous list refresh, non-breaking ticket summary, and display name when created_by is null)

v6.5.33
----------
 * Fix Add To Group bulk action on contact list page
 * Add status field to ContactImport and before starting batches, set redis key mailroom can use to track progress
 * Delete unused template and minor cleanup

v6.5.32
----------
 * Fix template indentation
 * Pass force=True when closing ticket as part of releasing a ticketer
 * Add beginings of new nav and SPA based UI (hidden from users for now)

v6.5.31
----------
 * Show masked urns for contacts API on anon orgs
 * Rework notifications, don't use Log model

v6.5.30
----------
 * Fix deleting of imports and exports now that they have associated logs

v6.5.29
----------
 * Add basic (and unused for now) JSON endpoint for listing notifications
 * Reduce sentry trace sampling to 0.01
 * Override kir language name
 * Add change_topic as action to ticket bulk actions API endpoint
 * Add Log and Notification model

v6.5.28
----------
 * Add new ticket event type for topic changes
 * Migrations to assign default topic to all existing tickets

v6.5.27
----------
 * Add migration to give all existing orgs a default ticket topic

v6.5.26
----------
 * Move mailroom_db data to external JSON file
 * Run CI tests with latest mailroom
 * Add ticket topic model and initialize orgs with a default topic

v6.5.25
----------
 * Improve display of channels logs for calls

v6.5.24
----------
 * Add machine detection as config option to channels with call role
 * Tweak event_fires management command to show timesince for events in the past

v6.5.23
----------
 * Drop retry_count, make error_count non-null
 * Improve channel log templates so that we use consistent date formating, show call error reasons, and show back button for calls
 * Tweak how we assert form errors and fix where they don't match exactly
 * Re-add QUEUED status for channel connections

v6.5.22
----------
 * Tweak index used for retrying IVR calls to only include statuses Q and E
 * Dont show ticket events like note added or assignment on contact read page
 * Include error reason in call_started events in contact history
 * Remove channel connection statuses that we don't use and add error_reason

v6.5.21
----------
 * Prevent saving of campaign events without start_mode
 * Improve handling of group lookups in contact list views
 * Add button to see channel error logs

v6.5.20
----------
 * Make ChannelConnection.error_count nullable so it can be removed
 * Cleanup ChannelConnection and add index for IVR retries
 * Fix error display on contact update modal
 * Update to zapier app directory, wide formax option and fixes
 * Enable filtering on the channel log to see only errors

v6.5.19
----------
 * Fix system group labels on contact read page
 * Use shared error messages for orgs being flagged or suspended
 * Update to latest smartmin (ignores _format=json on views that don't support it)
 * Add command to undo events from a flow start
 * Send modal should validate URNs
 * Use s3 when appropriate to get session output
 * Add basic user accounts API endpoint

v6.5.18
----------
 * Apply webhook ticket fix to successful webhook calls too

v6.5.17
----------
 * Tweak error message on flow start modal now field component is fixed
 * Fix issue for ticket window growing with url length
 * Update LUIS classifiers to work with latest API requirements
 * Tweak migration to populate contact.ticket_count so that it can be run manually
 * Switch from django.contrib.postgres.fields.JSONField to django.db.models.JSONField
 * Introduce s3 utility functions, use for reading s3 sessions in contact history

v6.5.16
----------
 * Update to Django 3.2
 * Migration to populate contact.ticket_count

v6.5.15
----------
 * Add warning to flow start modal that options have changed
 * Fix importing of dynamic groups when field doesn't exist

v6.5.14
----------
 * Update to latest cryptography 3.x
 * Add deep linking for tickets
 * Update db trigger on ticket table to maintain contact.ticket_count

v6.5.13
----------
 * Tweak previous data migration to work with migrate_manual

v6.5.12
----------
 * Migration to zeroize contact.ticket_count and make it non-null

v6.5.11
----------
 * Allow deletion of fields used by campaign events
 * Add last_activity_on to ticket folder endpoints
 * Add API endpoint for ticket bulk actions
 * Add nullable Contact.ticket_count field

v6.5.10
----------
 * Remove textit-whatsapp channel type
 * Show ticket counts on ticketing UI
 * Update to latest components with fixes for scrollbar and modax reuse
 * Use new generic dependency delete modal for contact fields

v6.5.9
----------
 * Add management command for listing scheduled event fires
 * Add index for ticket count squashing task
 * Add data migration to populate ticket counts
 * Add constraint to Msg to disallow sent messages without sent_on and migration to fix existing messages like that

v6.5.8
----------
 * Fix celery task name

v6.5.7
----------
 * Fix flow start modal when starting flows is blocked
 * Add more information to audit_es_group command
 * Re-save Flow.has_issues on final flow inspection at end of import process
 * Add squashable model for ticket counts
 * Add usages modal for labels as well
 * Update the WA API version for channel that had it set when added
 * Break out ticket folders from status, add url state

v6.5.6
----------
 * Set sent_on if not already set when handling a mt_dlvd relayer cmd
 * Display sent_on time rather than created_on time in Sent view
 * Only sample 10% of requests to sentry
 * Fix searching for scheduled broadcasts
 * Update Dialog360 API usage

v6.5.5
----------
 * Fix export page to use new filter to get non-localized class name for ids
 * Fix contact field update
 * Add searchable to trigger groups
 * Add option to not retry IVR calls
 * Add usages modal for groups
 * Tweak wording on flow start modal

v6.5.4
----------
 * Rework flow start modal to show options as exclusions which are unchecked by default
 * Change sent messages view to be ordered by -sent_on

v6.5.3
----------
 * Add Last Seen On as column to contact exports
 * Resuable template for dependency lists

v6.5.2
----------
 * Internal ticketer for all orgs

v6.5.1
----------
 * Cleanup Msg CRUDL tests
 * Cleanup squashable models
 * Apply translations in fr
 * Replace trigger folders with type specific filtered list pages so that they can be sortable within types

v6.4.7
----------
 * Update flow editor to include lone-ticketer submit fix
 * Fix pagination on the webhook results page

v6.4.6
----------
 * Update flow editor to fix not being able to play audio attachments in simulator

v6.4.4
----------
 * Start background flows with include_active = true
 * Update flow editor with MediaPlayer fix
 * Fix poetry content-hash to remove install warning
 * Update translations from transifex

v6.4.3
----------
 * Improve contact field forms
 * Fix urn sorting on contact update
 * Improve wording on forms for contact groups, message labels and flow labels
 * Improve wording on campaign form

v6.4.2
----------
 * Fix attachment button when attachments don't have extensions
 * Add missing ticket events to contact history
 * Fix clicking attachments in msgs view sometimes navigating to contact page
 * Parameterized form widgets. Bigger, darker form bits.
 * Tweak trigger forms for clarity
 * Add command to rebuild messages and pull translations from transifex

v6.4.1
----------
 * Fix unassigning tickets

v6.4.0
----------
 * Update README

v6.3.90
----------
 * Fix alias editor to post json

v6.3.89
----------
 * Remove beta grating of internal ticketers
 * Control which users can have tickets assigned to them with a permission
 * Use mailroom endpoints for ticket assignment and notes
 * Add custom user recover password view

v6.3.88
----------
 * Fix to display email on manage orgs
 * Drop no longer used Broadcast.is_active field

v6.3.87
----------
 * Update indexes on ticket model
 * Tweak ticketer default names
 * Add empty ticket list treatment
 * Fix API docs for messages endpoint to mention attachments rather than the deprecated media field
 * Update to editor with hidden internal ticketers
 * Consistent setting of modified_by when releasing/archiving/restoring
 * Remove old ticket views
 * Change ticketer sections on org home page to have Remove button and not link to old ticket views
 * Add assignee to ticketing endpoints, some new filters and new assignment view

v6.3.86
----------
 * Stop writing Broadcast.is_active as default value
 * Fix keyword triggers being imported without a valid match_type

v6.3.85
----------
 * User the current user as the manual trigger user during simulation
 * Better trigger exports and imports
 * Make broadcast.is_active nullable and stop filtering by it in the API

v6.3.84
----------
 * Ignore scheduled triggers in imports because they don't import properly
 * Fix redirect after choosing an org for users that can't access the inbox
 * Optionally filter ticket events by ticket in contact history view

v6.3.83
----------
 * Fix default content type for pjax requests
 * Tweak queuing of flow starts to include created_by_id

v6.3.82
----------
 * Revert recent formax changes

v6.3.81
----------
 * Add Broadcast.ticket and expose as field (undocumented for now) on broadcast write API endpoint
 * Refactor scheduling to use shared form
 * Add exclusion groups to scheduled triggers

v6.3.80
----------
 * Update components so omnibox behaves like a field
 * Drop Language model and Org.primary_language field

v6.3.79
----------
 * Order tickets by last_activity_on and update indexes to reflect that
 * Backfill ticketevent.contact and use that for fetching events in contact history
 * Fix creating scheduled triggers not being able to see week day options
 * Handle reopen events for tickets
 * Stop creating Language instances or setting Org.primary_language

v6.3.78
----------
 * Add Ticket.last_activity_on and TicketEvent.contact
 * Rreturn tickets by modified_on in the API
 * Add ability to reverse results for runs/contacts API endpoints

v6.3.77
----------
 * Better validation of invalid tokens when claiming Zenvia channels
 * Fix languages formax to not allow empty primary language

v6.3.76
----------
 * Read org languages from org.flow_languages instead of Language instances

v6.3.75
----------
 * Fix closing and reopening of tickets from API

v6.3.74
----------
 * Add better labels and help text for groups on trigger forms
 * Load ticket events from database for contact histories
 * Fix rendering of closed ticket triggers on trigger list page
 * Fix rendering of ticket events as JSON
 * Fix for delete modals

v6.3.73
----------
 * Backfill ticket open and close events
 * Add support for closed ticket triggers

v6.3.72
----------
 * Add CSRF tokens to modaxes

v6.3.70
----------
 * Add CSRF token to modax form
 * Tweak padding for nav so we don't overlap alerts
 * Only require current password to change email or password
 * Fix icon colors on latest chrome
 * Migration to backfill Org.flow_languages

v6.3.69
----------
 * Add Org.flow_languages and start populating in Org.set_languages
 * Raise the logo so it can be clicked

v6.3.68
----------
 * Enable exclusion groups on triggers and make groups an option for all trigger types
 * Add users to mailroom test db
 * Add ticket note support to UI

v6.3.67
----------
 * Pass user id to ticket/close ticket/reopen endpoints to use in the TicketEvent mailroom creates
 * Model changes for ticket assignment
 * Make flow session output URL have a max length of 2048

v6.3.66
----------
 * Add new ticket event model
 * Add output_url field to FlowSession

v6.3.65
----------
 * Fix rendering of recipient buttons on outbox
 * Rework trigger create forms to make conflict handling more consistent
 * Iterate through all pages when syncing whatsapp templates

v6.3.64
----------
 * URL field on HTTPRequestLog should have max length of 2048

v6.3.63
----------
 * Drop unused index on contact name, and add new org+modified_on index

v6.3.62
----------
 * Update components to single mailroom resource for completion

v6.3.60
----------
 * Only retry 5000 messages at a time, prefetch channel and fields

v6.3.59
----------
 * Enable model instances to show an icon in selects

v6.3.58
----------
 * Add model changes for closed ticket triggers
 * Add model changes for exclude groups support on triggers

v6.3.57
----------
 * Tweak mailroom_db to make contact created_on values fixed
 * Add trigger type folder list views
 * Fix filtering of flows for new conversation triggers
 * Fix ordering of channel fields on triggers
 * Tweak inspect_flows command to handle unreadable flows
 * Nest group buttons on campaign list so they don't grow to largest cell

v6.3.56
----------
 * Fix migrating flows whose definitions contain decimal values
 * Update to tailwind 2, fix security warnings
 * Simplify org filtering on CRUDLs
 * Remove IS_PROD setting

v6.3.55
----------
 * Update layout and color for badge buttons
 * Add management command to inspect flows and fix has_issues where needed
 * Fix deleting flow labels with parents
 * Fix broken org delete modal
 * Add user arg to Org.release and User.release

v6.3.54
----------
 * Optimize message retries with a perfect index
 * Convert channels to soft dependencies

v6.3.53
----------
 * Update to latest temba-components

v6.3.52
----------
 * Update to latest floweditor
 * Adjust WA templates page title
 * Fix Dialog360 WA templates sync

v6.3.51
----------
 * Adjust WA templates page styles
 * Migration to clear next_attempt for android channels

v6.3.50
----------
 * Resend messages using web endpoint rather than task
 * Convert message labels, globals and classifiers to use soft dependencies

v6.3.49
----------
 * Make Msg.next_attempt nullable and add msgs to mailroom_db
 * Migration to ensure that inactive flows don't have any deps
 * Fix Flow.release to remove template deps

v6.3.48
----------
 * Calculate proper msg id commands from relayer that have integer overflow issue
 * Add reusable view for dependency deleting modals and switch to that and soft dependencies for ticketers
 * Don't do mailroom session interruption during org deletion
 * Fix org deletion when broadcasts have parents and webhook results have contacts
 * Make sure templates and templates translations are deleted on org release
 * Set max fba pages limit to 200

v6.3.47
----------
 * Display warning icon in flow list for flows with issues
 * Make Flow.has_issues non-null and cleanup unused localized strings on Flow model
 * Support syncing Dialog360 Whatsapp templates

v6.3.46
----------
 * Fix channel log icons and disallow message resending for suspended orgs
 * Add migration to populate Flow.has_issues

v6.3.45
----------
 * Add migration to populate template namespace
 * Expose template translation namespace field on API
 * Don't save issues into flow metadata but just set new field has_issues instead
 * Queue mailroom task to do msg resends

v6.3.44
----------
 * Tweak import preview page so when adding to a group isn't enabled, the group controls are disabled
 * Update flow editor and temba-components

v6.3.40
----------
 * Add namespace field to template translations
 * Fetching and saving revisions should return flow issues as separate field

v6.3.39
----------
 * Rework task for org deletion

v6.3.38
----------
 * Move tickets endpoint to tickets crudl
 * Refactor WhatsApp templates
 * Add task for releasing of orgs

v6.3.37
----------
 * Fix contact imports always creating new groups
 * Migration to fix escaped nulls in flow revision definitions
 * Rework beta gated agent views to be tikect centric

v6.3.35
----------
 * Clear primary language when releasing org
 * Strip out NULL characters when serializing JsonAsTextField values
 * Override language names and ensure overridden names are used for searching and sorting

v6.3.33
----------
 * Update components and flow editor to common versions
 * Allow external ticketers to use agent ui, add footer to tickets

v6.3.32
----------
 * Release import batches when releasing contact imports

v6.3.31
----------
 * Fix serializing JSON to send to mailroom when it includes decimals

v6.3.30
----------
 * Restrict org languages to ISO-639-1 plus explicit inclusions

v6.3.29
----------
 * Move Twilio, Plivo and Vonage number searching views into their respective channel packages
 * Optimize query for fetching contacts with only closed tickets
 * Release contact imports when releasing groups
 * Proper skip anonymous user for analytics

v6.3.28
----------
 * Remove simplejson
 * Update to latest vonage client and fix retries

v6.3.27
----------
 * Restore menu-2 icon used by org choose menu

v6.3.26
----------
 * Make groups searchable on contact update page

v6.3.25
----------
 * Add beta-gated tickets view

v6.3.24
----------
 * Change analytics.track to expect a user argument
 * Add org released_on, use when doing full releases
 * Ignore anon user in analytics

v6.3.23
----------
 * Clean up countries code used by various channel types

v6.3.22
----------
 * Show results in flow order

v6.3.21
----------
 * Fix Javascript error on two factor formax
 * Beta-gate chatbase integration for now

v6.3.20
----------
 * Rework DT One and Chatbase into a new integrations framework
 * Expose Org.language as default language for new users on org edit form

v6.3.19
----------
 * Add support for Zenvia SMS
 * Cleanup parsing unused code on org model
 * Fix flow update forms to show correct fields based on flow type
 * Tweak JSONAsTextField to allow underlying DB column to be migrated to JSONB
 * Add controls to import preview page for selecting existing groups etc

v6.3.18
----------
 * Fix template names

v6.3.17
----------
 * Fix font reference in scss

v6.3.16
----------
 * Add group name field to contact imports so that it can be customized
 * Rename Nexmo to Vonage, update icon
 * Merge the two used icomoon sets into one and delete unused one
 * Cleanup problems in org view templates

v6.3.15
----------
 * Revert wording changes when orgs don't have email settings to clarify that we do send
 * Fix wording of Results link in editor

v6.3.14
----------
 * Fix locale files
 * Fix SMTP server settings views to explain that we don't send emails if you don't have a config
 * Add API endpoint to fetch tickets filterable by contact

v6.3.13
----------
 * Clarify terms for exports vs downloads
 * Fix rendering of airtime events in contact history
 * Add flows import and flow exports links in the flows tab

v6.3.12
----------
 * Update to latest flow-editor
 * Cleanup unused dates methods
 * Update markdown dependency
 * Expose exclude_active on flow start read API
 * Support 3 digits short code on Jasmin channel type
 * Add support for YYYY-MM-DD date format
 * Update DT One support to collect api key and secret to use with new API
 * Update parent remaining credits
 * Release broadcasts properly

v6.3.11
----------
 * Fix redirect after submitting Start In Flow modal

v6.3.10
----------
 * Add support to exclude active contacts in other flows when starting a flow on API
 * Remove unsupported channel field on broadcast create API endpoint
 * Add Start Flow modal to contact read page
 * Fix lock file being out of sync with pyproject

v6.3.9
----------
 * Revert update to use latest API version to get WA templates
 * Fix setting Zenvia webhooks
 * Update Django and Django REST Framework

v6.3.8
----------
 * Convert to poetry

v6.3.6
----------
 * Update pt_BR translation
 * Update to use latest API version to get WA templates
 * Display failed on flow results charts, more translations
 * Zenvia WhatsApp

v6.3.5
----------
 * Fix broken flow results charts

v6.3.4
----------
 * Update to latest celery 4.x

v6.3.2
----------
 * Support reseting the org limits to the default settings by clearing the form field
 * Update redis client to latest v3.5.3
 * Fix manage accounts form blowing up when new user has been created in background

v6.3.1
----------
 * Add support for runs with exit_type=F
 * Support customization for org limits

v6.3.0
----------
 * Update stable versions and coverage badge link
 * Style Outbox broadcasts with megaphone icons and use includes for other places we render contacts and groups
 * Fix spacing on outbox view
 * Add discord channel type

v6.2.4
----------
 * Update Portuguese translation
 * Update to floweditor v1.13.5

v6.2.3
----------
 * Update to latest floweditor v1.13.4

v6.2.2
----------
 * Update to flow editor v1.13.3
 * Update Spanish translation
 * Disable old Zenvia channel type
 * Fix styles on fields list

v6.2.1
----------
 * Return registration details to Android if have the same UUID
 * Add spacing between individual channel log events
 * Fix external channel claim form
 * Do not track Android channels creation by anon user

v6.2.0
----------
 * Update translations for es, fr and pt-BR
 * Fix rendering of pending broadcasts in outbox view

v6.1.48
----------
 * Update editor with dial router changes
 * Fix resthook formax validation

v6.1.47
----------
 * Change synched to synced
 * Update to smartmin 2.3.5
 * Require recent authentication to view backup tokens

v6.1.46
----------
 * Update to smartmin 2.3.5
 * Fix handling of attempts to sync old unclaimed channels
 * Add view to list all possible channel types
 * Fix rendering of nameless channels

v6.1.45
----------
 * Open up 2FA to all users
 * Do not allow duplicates invites
 * Never respond with registration commands in sync handler

v6.1.44
----------
 * Enforce time limit between login and two factor verification
 * Prevent inviting existing users
 * Add disabled textinputs and better expression selection on selects
 * Create failed login records when users enter incorrect backup tokens too many times
 * Logout user to force login to accept invite and require invite email account exactly

v6.1.43
----------
 * Backup tokens can only be used once
 * Add new 2FA management views

v6.1.42
----------
 * Use Twilio API to determine capabilities of new Twilio channels
 * Fix result pages not loading for users using Spanish interface

v6.1.41
----------
 * Remove no longer used permissions
 * Override login view to redirect to new views for two-factor authentication
 * Reduce recent export window to 4 hours
 * Change message campaign events to use background flows

v6.1.40
----------
 * Remove UserSettings.tel and add UserSettings.last_auth_on

v6.1.39
----------
 * Increase max len of URN fields on airtime transfers
 * Add toggle to display manual flow starts only
 * Cleanup 2FA models

v6.1.38
----------
 * Update flow editor to 1.12.10 with failsafe errors
 * Make validation of external channel URLs disallow private and link local hosts
 * Cleanup middleware used to set org, timezone and language

v6.1.37
----------
 * Update components and editor to latest versions
 * Switch to microsecond accuracy timestamps
 * Switch to default_storage for export assets 

v6.1.33
----------
 * Tweaks to how we generate contact histories

v6.1.32
----------
 * Mute invalid host errors
 * Add migration to alter m2ms to use bigints
 * Drop no longer used database function
 * Switch to big id for msgs and channel logs

v6.1.31
----------
 * Add management command to check sentry
 * Remove unused context processor and unused code from org_perms

v6.1.29
----------
 * Rework contact history so that rendering as events happens in view and we also expose a JSON version

v6.1.26
----------
 * Upgrade urllib3

v6.1.25
----------
 * Update to elastic search v7

v6.1.24
----------
 * Broadcast events in history should be white like message events

v6.1.23
----------
 * Add index on flow start by start type
 * Allow only deleting msg folders without active children labels
 * Use engine events (with some extra properties) for msgs in contact history

v6.1.22
----------
 * Fix API serialization of background flow type
 * Allow background flows to be used in scheduled triggers
 * Update pip-tools

v6.1.21
----------
 * Configure editor and components to use completions files in current language

v6.1.20
----------
 * Update to latest floweditor and temba-components

v6.1.19
----------
 * Update to floweditor v1.12.6
 * Fix deleting classifiers

v6.1.18
----------
 * Add support for background flows

v6.1.17
----------
 * Update to flow editor v1.12.5
 * Fix importing dependencies when it's a clone in the same workspace
 * Allow aliases to be reused on boundaries with different parent
 * Increase max length on external channels to be configurable up to 6400 chars
 * Fix contact export warning for existing export

v6.1.16
----------
 * Update to latest flow editor 1.12.3
 * Allow staff users to use the org chooser

v6.1.15
----------
 * Add constraint to chek URN identity mathes scheme and path
 * Add non-empty constraint for URN scheme and path
 * Fix contact list pagination with searches
 * Show query on list page for smart groups

v6.1.14
----------
 * Change template translations to be TEXT
 * Set global email timeout, fixes rapidpro #1345
 * Update tel parsing to match gocommon, fixing how we currently accept local US numbers

v6.1.13
----------
 * Bump temba-components to v0.8.11

v6.1.12
----------
 * Un-beta-gate Rocket.Chat channels

v6.1.10
----------
 * Login summary on org home page should include agents
 * Rework manage accounts UI to include agents

v6.1.9
----------
 * Fix deleted flow dependency preventing global deletion
 * Cache lookups of auth.Group instances

v6.1.8
----------
 * For field columns in imports, only match against user fields
 * Add agent role and cleanup code around org roles

v6.1.7
----------
 * Wire table listeners on pjax reload
 * Update domain from swag.textit.com to whatsapp.textit.com
 * Add internal ticketer type for BETA users
 * Inner scrolling on contact list page
 * Improve styles for recipient lists

v6.1.6
----------
 * Trim our start runs 1,000 at a time and by id
 * Increase global max value length to 10000 and fix UI to be more consistent with fields

v6.1.5
----------
 * Share modals on globals list, truncate values
 * Squash migrations

v6.1.4
----------
 * Add security settings file
 * Fix intent selection on split by intent
 * Add empty migrations for squashing in next release

v6.1.3
----------
 * Fix intent selection on split by intent
 * Update callback URL for textit whatsapp
 * Use Django password validators

v6.1.2
----------
 * Add TextIt WhatsApp channel type

v6.1.1
----------
 * Fix contact exports when orgs have orphaned URNs in schemes they don't currently use

v6.1.0
----------
 * Hide editor language dialog blurb until needed to prevent flashing
 * Fix broken flows list page if org has no flows
 * Allow underscores in global names
 * Improve calculating of URN columns for exports so tests don't break every time we add new URN schemes
 * Make instruction lists on channel claim pages more consistent

v6.0.8
----------
 * Editor fix for split by intents
 * Add empty migrations for squashing in next release

v6.0.7
----------
 * Fix choose org page
 * Fix recipient search
 * Fix run deletion

v6.0.6
----------
 * Fix for textarea init

v6.0.5
----------
 * Adjust contact icon color in recipient lists

v6.0.4
----------
 * Fix recipients contacts and urns UI labels
 * Fix flow starts log page pagination
 * Update temba-components and flow-editor to common versions
 * Fix flow label delete modal
 * Fix global delete modal

v6.0.3
----------
 * Update to components v0.8.6, bugfix release
 * Handle CSV imports in encodings other than UTF8

v6.0.2
----------
 * Fix broken ticket re-open button
 * Missing updated Fr MO file from previous merge
 * Apply translations in fr

v6.0.1
----------
 * Fix orgs being suspended due to invalid topup cache
 * Set uses_topups on new orgs based on whether our plan is the TOPUP_PLAN
 * Fix validation issues on trigger update form
 * Fix hover cursor in lists for viewers
 * Action button alignment on archived messages
 * Fix flow table header for viewers
 * Fix tests for channel deletion
 * Fix redirects for channel and ticketer deletion.
 * Fix dialog when deleting channels with dependencies
  * Match headers and contact fields with labels as well as keys during contact imports

v6.0.0
----------
 * Add Rocket.Chat ticketer to test database

v5.7.91
----------
 * Add Rocket.Chat ticketers

v5.7.90
----------
 * Update rocket.chat icon in correct font

v5.7.89
----------
 * Improve Rocket.Chat claim page
 * Add Rocket.Chat icon

v5.7.87
----------
 * Cleanup Rocket.Chat UI

v5.7.86
----------
 * Add RocketChat channels (beta-only for now)

v5.7.85
----------
 * Add back jquery-migrate and remove debug

v5.7.84
----------
 * Remove select2, coffeescript, jquery plugins

v5.7.83
----------
 * Fix broken import link on empty contacts page
 * Use consistent approach for limits on org
 * Globals UI should limit creation of globals to org limit
 * Fix archives list styles and add tabs for message and run archives
 * Restyle the Facebook app channel claim pages
 * Switch to use FBA type by default

v5.7.82
----------
 * Don't blow up if import contains invalid URNs but pass values on to mailroom
 * Update to version of editor with some small styling tweaks
 * Include occurred_on with mo_miss events queued to mailroom
 * Adjust Twilio connect to redirect properly to the original claim page
 * Remove no longer used FlowRun.timeout_on and drop two unused indexes
 * Cleanup more localized strings with trimmed
 * Fix 404 error in channel list

v5.7.81
----------
 * Add page title to brand so that its configurable
 * Dont send alert emails for orgs that aren't using topups
 * Consider timezone when infering org default country and display on import create page
 * Add page titles to fields and flows
 * Allow changing EX channels role on UI

v5.7.80
----------
 * Add contact last seen on to list contacts views
 * Cleanup channel model fields
 * Add charcount to send message dialog
 * Show channel logs link for receive only channels
 * Fix export flow page styles
 * Allow searching for countries on channel claim views

v5.7.79
----------
 * Rework imports to allow importing multiple URNs of same scheme
 * Cleanup no longer used URN related functionality
 * Show contact last seen on on contact read page

v5.7.78
----------
 * Clean up models fields in contacts app

v5.7.77
----------
 * Fix styling on the API explorer page
 * Fix list page selection for viewers
 * Move contact field type constants to ContactField class
 * Allow brand to be set by env variable

v5.7.76
----------
 * Drop support for migrating legacy expressions on API endpoints
 * Fix imports blowing up when header is numerical
 * Fix 11.4 flow migration when given broken send action
 * Drop RuleSet and ActionSet models

v5.7.75
----------
 * Last tweaks before RuleSet and ActionSet can be dropped
 * Contact id treatment for details
 * Update components to ship ajax header and use it in language endpoint
 * Remove no longer needed legacy editor completion

v5.7.74
----------
 * Remove legacy flow code
 * WA channel tokens refresh catch errors for each channel independently

v5.7.73
----------
 * Make flows searchable and clickable on triggers
 * Make flows searchable on edit campaign event

v5.7.72
----------
 * Fix editor whatsapp templates, refresh whatsapp channel pages
 * Move omnibox module into temba.contacts.search

v5.7.71
----------
 * Remove legacy contact searching
 * Remove code for dynamic group reevaluation and campaign event scheduling

v5.7.70
----------
 * Fix pdf selection

v5.7.69
----------
 * Validate language codes passed to contact API endpoint
 * Don't actually create a broadcast if sending to node but nobody is there
 * Update to latest floweditor

v5.7.67
----------
 * Fix globals endpoint so name is required
 * Filter by is_active when updating fields on API endpoint

v5.7.66
----------
 * Replace remaining Contact.get_or_create calls with mailroom's resolve endpoint

v5.7.65
----------
 * URN lookups onthe contact API endpoint should be normalized with org country
 * Archiving a campaign should only recreate events

v5.7.64
----------
 * Don't create contacts and URNs for broadcasts but instead defer the raw URNs to mailroom

v5.7.63
----------
 * Validate that import files don't contain duplicate UUIDs or URNs

v5.7.62
----------
 * Update version of editor and components
 * Upload imports to use UUID based path
 * Fix issue where all keywords couldnt be removed from a flow

v5.7.61
----------
 * Remove old editor, redirect editor_next to editor

v5.7.60
----------
 * Fix contact imports from CSV files
 * Tweaks to import UI

v5.7.59
----------
 * Imports 2.0

v5.7.55
----------
 * Use v13 flow as example on definitions endpoint docs
 * Add URNs field to FlowStart and pass to mailroom so that it creates contacts

v5.7.54
----------
 * Update editor to get support for expressions in add to group actions
 * Remove unused localized text on Msg and Broadcast

v5.7.52
----------
 * Migrations and models for new imports

v5.7.51
----------
 * Add plan_start, calculate active contacts in plan period, add to OrgActivity
 * Tweak how mailroom_db creates extra group contacts
 * Update to latest django-hamlpy

v5.7.50
----------
 * Optimizations for orgs with many contact fields

v5.7.49
----------
 * Update plan_end when suspending topup orgs
 * Suspend topup orgs that have no active credits
 * Show suspension header when an org is suspended
 * Tweak external channel config styling
 * Fix styles for button on WA config page

v5.7.48
----------
 * Fix button style for channel extra links
 * Skip components missing text for WA templates sync
 * Editors should have API tokens

v5.7.47
----------
 * Queue mailroom task to schedule campaign events outside of import transaction
 * Fix margin on fields warning alert

v5.7.46
----------
 * Use mailroom task for scheduling of campaign events

v5.7.45
----------
 * Make sure form._errors is a list

v5.7.44
----------
 * Add index to enforce uniqueness for event fires

v5.7.43
----------
 * Fix migration

v5.7.42
----------
 * Bump smartmin to 2.2.3
 * Fix attachment download and pdf links

v5.7.41
----------
 * Fix messages to send without topup, and migrations
 * No topup transfers on suborgs, show contacts, not credits

v5.7.40
----------
 * Invalid language codes passed to contact API endpoint should be ignored and logged for now

v5.7.39
----------
 * Update widget focus and borders on legacy editor
 * Show global form errors and pre-form on modax template

v5.7.38
----------
 * Add alpha sort and search to results view
 * Searchable contact fields and wired listeners after group changes
 * Force policy redirect on welcome page, honor follow-on navigation redirect
 * Use mailroom for contact creation in API and mailroom_db command
 * Adjust styling for contact import scenarios
 * Show address when it doesn't match channel name

v5.7.37
----------
 * add topup button to topup manage page

v5.7.36
----------
 * Fix deleting ticketers

v5.7.35
----------
 * Zendesk file view needs to be csrf exempt
 * Use mailroom to create contacts from UI

v5.7.34
----------
 * Add view to handle file URL callbacks from Zendesk

v5.7.33
----------
 * Fix delete button on archived contacts page
 * Don't allow saving queries that aren't supported as smart groups
 * Delete no longer used contacts/fields.py
 * Fix contacts reppearing in ES searches after being modified by a bulk action
 * Adjust pjax block for contact import block

v5.7.32
----------
 * Modal max-height in vh to not obscure buttons

v5.7.31
----------
 * Add padding for p tags on policies

v5.7.30
----------
 * Add content guideline policy option, update styling a bit

v5.7.29
----------
 * Sitewide refresh of styles using Tailwind

v5.7.27
----------
 * Site refresh of styles using Tailwind.

v5.7.28
----------
 * Update to flow editor v1.9.15

v5.7.27
----------
 * Update to flow editor v1.9.14
 * Add support for last_seen_on in legacy search code

v5.7.26
----------
 * Handle large deletes of contacts in background task

v5.7.25
----------
 * Fix bulk actions against querysets from ES searches
 * Fix bulk action permissions on contact views

v5.7.24
----------
 * Rename existing 'archive' contact action in API to 'archive_messages'
 * Allow deleting of all contacts from Archived view

v5.7.23
----------
 * Rename All Contacts to Active
 * Add UI for archiving, restoring and deleting contacts

v5.7.22
----------
 * Bump version of mailroom and indexer used for tests
 * Drop no longer used is_blocked and is_stopped fields

v5.7.21
----------
 * Add missing migration from last rev

v5.7.20
----------
 * Add missing migration

v5.7.19
----------
 * Make contact.is_stopped and is_blocked nullable and stop writing

v5.7.18
----------
 * Update sys group trigger to handle archiving

v5.7.17
----------
 * Migration to add Archived sys group to all orgs

v5.7.16
----------
 * Update to flow editor 1.9.11
 * Update database triggers to use contact status instead of is_blocked or is_stopped
 * Make contact.status non-null
 * Create new archived system group for new orgs

v5.7.15
----------
 * Add nag warning to legacy editor

v5.7.14
----------
 * Migration to backfill contact status

v5.7.13
----------
 * Enable channelback files for Zendesk ticketers
 * Set status as active for new contacts
 * Add new status field to contact
 * Fix legacy editor by putting html-tag block back
 * Change the label for CM channel claim

v5.7.12
----------
 * Fix imports that match by UUID
 * Fix Nexmo search numbers and claim number
 * Use Django language code on html tag
 * Add support for ClickMobile channel type

v5.7.11
----------
 * Fix creating of campaign events based on last_seen_on
 * Tweak msg_console so it can include sent messages which are not replies
 * Fix mailroom_db command
 * Expose last_seen_on on contact API endpoint

v5.7.10
----------
 * Update floweditor to 1.9.10
 * Add Last Seen On as a system field so it can be used in campaigns
 * Tweak search_archives command to allow JSONL output

v5.7.9
----------
 * Fix reading of S3 event streams
 * Migration to populate contact.last_seen_on from msg archives

v5.7.8
----------
 * Add plan_end field to Orgs

v5.7.7
----------
 * Add search archives management command

v5.7.6
----------
 * Optimizations to migration to backfill last_seen_on

v5.7.5
----------
 * Add migration to populate contact.last_seen_on
 * Update to latest temba-components with support for refresh work

v5.7.4
----------
 * Use new metadata field from mailroom searching endpoints
 * Make sure we have only one active trigger when importing flows
 * Fix org selector and header text alignment when editor is open

v5.7.3
----------
 * Add contact.last_seen_on
 * Bump floweditor to v1.9.9

v5.7.2
----------
 * Add error messages for all error codes from mailroom query parsing
 * Fix org manage quick searches
 * Always use mailroom for static group changes

v5.7.1
----------
 * Add session history field to flowstarts
 * Have mailroom reset URNs after contact creation to ensure order is correct

v5.7.0
----------
 * Add start_type and created_by to queued flow starts
 * New mixin for list views with bulk actions
 * Update some dependencies to work with Python 3.8 and MacOS

v5.6.5
----------
 * Set the tps options for Twilio based on country and number type
 * Fix wit.ai classifiers and double logging of errors on all classifier types

v5.6.3
----------
 * Add variables for nav colors

v5.6.2
----------
 * Fix failing to manage logins when the we are logged in the same org

v5.6.1
----------
 * instead of dates, keep track of seen runs when excluding archived runs from exports

v5.6.0
----------
 * 5.6.0 Release Candidate

v5.5.78 
----------
 * Improve the visuals and guides on the FBA claim page
 * Block flow starts and broadcasts for suspended orgs
 * Add a way to suspend orgs from org manage page

v5.5.77
----------
 * Subscribe to the Facebook app for webhook events

v5.5.76
----------
 * Add Facebook App channel type

v5.5.75
----------
 * always update both language and country if different

v5.5.74
----------
 * allow augmentation of templates with new country

v5.5.73
----------
 * Add support for urn property in search queries
 * Add support for uuid in search queries
 * Set country on WhatsApp templates syncing and add more supported languages
 * Add country on TemplateTranslation

v5.5.72
----------
 * Use modifiers for field value updates

v5.5.71
----------
 * Fix to allow all orgs to import flows

v5.5.70
----------
 * Use modifiers and mailroom to update contact URNs

v5.5.69
----------
 * Refresh contact after letting mailroom make changes
 * Contact API endpoint can't call mailroom from within a transaction

v5.5.68
----------
 * Fix contact update view
 * Allow multi-user / multi-org to be set on each org
 * Fix additional urls import

v5.5.66
----------
 * Implement Contact.update_static_groups using modifiers
 * Consistent use of account/login/workspace

v5.5.64
----------
 * Fix editor

v5.5.63
----------
 * Make new org fields non-null and remove no longer needed legacy method

v5.5.62
----------
 * Rename whitelisted to verified
 * Add migration to populate new org fields

v5.5.61
----------
 * Add new boolean fields to org for suspended, flagged and uses_topups and remove no longer used plan stuff

v5.5.60
----------
 * Move webhook log button to flow list page
 * Add confirmation dialog to handle flow language change

v5.5.59
----------
 * Update to floweditor v1.9.8

v5.5.58
----------
 * Update to floweditor 1.9.7
 * Remove BETA gating for tickets

v5.5.57
----------
 * Restore logic for when dashboard and android nav icons should appear
 * Add translations in ru and fr

v5.5.56
----------
 * Improvements to ticketer connect views
 * Still need to allow word only OSM ids

v5.5.55
----------
 * Fix boundaries URL regex to accept more numbers

v5.5.54
----------
 * Add index for mailroom looking up tickets by ticketer and external ID
 * Make it easier to differentiate open and closed tickets
 * Update to temba-components 0.1.7 for chrome textinput fix

v5.5.53
----------
 * Add indexes on HTTP log views
 * Simplify HTTP log views for different types whilst given each type its own permission

v5.5.52
----------
 * More ticket view tweaks

v5.5.51
----------
 * Tweak zendesk manifest view

v5.5.50
----------
 * Tweak zendesk mailroom URLs

v5.5.49
----------
 * Store brand name in mailgun ticketer config to use in emails from mailroom

v5.5.48
----------
 * Defer to mailroom for ticket closing and reopening

v5.5.47
----------
* Beta-gated views for Mailgun and Zendesk ticketers 

v5.5.46
----------
 * Bump black version
 * Fix layering of menu with simulator

v5.5.45
----------
 * Increase the template name field to accept up to 512 characters
 * Make sending of Stripe receipts optional
 * Add OrgActivity model that tracks contacts, active contacts, incoming and outgoing messages

v5.5.43
----------
 * Fix JS escaping on channel log page

v5.5.42
----------
 * Remove csrf exemption for views that don't need it (all our pjax includes csrf)
 * Escape translations in JS literals
 * Upgrade FB graph API to 3.3

v5.5.41
----------
 * Use branding keys when picking which orgs to show on manage

v5.5.40
----------
 * Allow branding to have aliases
 * Fix bug of removing URNs when updating fields looking up by URN

v5.5.39
----------
 * Update to floweditor 1.9.6
 * New task to track daily msgs per user for analytics
 * Add support for Russian as a UI language
 * Models and editor API endpoint for tickets
 * Skip duplicate relayer call events

v5.5.38
----------
 * Update to flow editor 1.9.5
 * Allow custom TS send URLs

v5.5.37
----------
 * Remove all uses of _blank frame name
 * Strip exif data from images

v5.5.36
----------
 * Better tracking of channel creation and triggers, track simulation
 * Do not use font checkboxes for contact import extra fields

v5.5.35
----------
 * Revert Segment.io identify change to stay consistent with other tools

v5.5.34
----------
 * Identify users in Segment.io using best practice of user id, not email

v5.5.33
----------
 * Add context processor to stuff analytics keys into request context
 * Restrict 2FA functionality to BETA users

v5.5.32
----------
 * Add basic 2FA support

v5.5.31
----------
 * Update to latest smartmin

v5.5.30
----------
 * Add new flow start type to record that flow was started by a Zapier API call
 * Contact bulk actions endpoint should error if passed no contacts
 * Remove mentioning the countries for AT claim section
 * Add Telesom channel type

v5.5.29
----------
 * Fix trimming flow starts with start counts

v5.5.28
----------
 * Update Africa's Talking supported countries

v5.5.27
----------
 * Remove temporary NOOP celery tasks
 * Drop Contact.is_paused field
 * Editor 1.9.4, better modal centering

v5.5.26
----------
 * Add NOOP versions of renamed celery tasks to avoid problems during deploy

v5.5.23
----------
 * Remove default value on Contact.is_paused so it can be dropped
 * Trim completed mailroom created flow starts
 * Update flow starts API endpoint to only show user created flow starts and add index

v5.5.22
----------
 * Add nullable contact.is_paused field
 * Display run count on flow start list page

v5.5.21
----------
 * Optimze flow start list page with DB prefetching
 * Indicate on flow start list page where start was created by an API call

v5.5.20
----------
 * Use actual PO library to check for msgid differences
 * Migration to backfill FlowStart.start_type
 * Log error of WA channel failing to sync templates

v5.5.19
----------
 * Add FlowStart.start_type
 * Ensure flow starts created via the API are only sent to mailroom after the open transaction is committed

v5.5.18
----------
 * Add flow start log page

v5.5.17
----------
 * Add index to list manually created flow starts
 * Make FlowStart.org and modified_on non-NULL
 * Move contact modification for name and language to be done by mailroom

v5.5.16
----------
 * bower no longer supported for package installs
 * Migration to backfill FlowStart.org and modified_on

v5.5.15
----------
 * Update to flow-editor 1.9.2, security patches

v5.5.14
----------
 * Ensure IVR retry is preserved on new revisions
 * Import flows for mailroom test db as v13
 * Make UUID generation fully mockable
 * Add run UUID on flow results exports
 * Drop unused fields on FlowStart and add org

v5.5.13
----------
 * Stop using FlowStart.modified_on so that it can be removed
 * Disable syncing templates with variables in headers and footers

v5.5.12
----------
 * Import and export of PO files

v5.5.10
----------
 * Bump up the simulator when popped so it fits on more screens
 * Editor performance improvements

v5.5.8
----------
 * Update help text on contact edit dialog
 * Add prometheus endpoint config on account page
 * Fix boundary aliases filtering by org

v5.5.7
----------
 * Fix open modal check on pjax refersh
 * Show warnings on contact field page when org is approaching the limit and has hit the limit

v5.5.6
----------
 * Temporaly disable templates requests to FB when claiming WA channels

v5.5.5
----------
 * newest smartmin with BoM fix

v5.5.4
----------
 * Show better summary of schedules on trigger list page
 * Fix display of trigger on contact group delete modal

v5.5.3
----------
 * Update to floweditor 1.8.9
 * Move EX constants to channel type package
 * Remove unused deps and address npm security warnings
 * Add 18 hours as flow expiration option
 * FlowCRUDL.Revisions should return validation errors from engine as detail field
 * Allow setting authentication header on External channels
 * Add normalize contact tels task
 * Drop full resolution geometry, only keep simplified
 * Add attachments columns to flow results messages sheet

v5.5.0
----------
 * Increase the WA channels tps to 45 by default

v5.4.13
----------
 * Fix URL related test errors

v5.4.12
----------
 * Don't allow localhost for URL fields

v5.4.11
----------
 * Make sure external channel URLs are external

v5.4.10
----------
 * Complete FR translations
 * Update to floweditor 1.8.8

v5.4.9
----------
 * Fix submitting API explorer requests where there is no editor for query part
 * Lockdown redirects on exports
 * Add more detailed fresh chat instructions

v5.4.8
----------
 * Find and fix more cases of not filtering by org

v5.4.7
----------
 * Fix org filtering on updates to globals
 * Fix campaign event update view not filtering by event org
 * Fix error in API contact references when passed a JSON number
 * Replace Whatsapp by WhatsApp

v5.4.6
----------
 * Merge pull request #2718 from nyaruka/fe187

v5.4.4
----------
 * fix various filtering issues

v5.4.3
----------
 * Update sample flow test

v5.4.2
----------
 * remove use of webhook where not appropriate

v5.4.1
----------
 * Update sample flows to use @webhook instead of @legacy_extra

v5.4.0
----------
 * Add API endpoint to update Globals
 * Keep latest sync event for Android channels when trimming

v5.3.64
----------
 * Add support for Twilio Whatsapp channel type

v5.3.63
----------
 * Add pre_deploy command to check imports/exports
 * Fix link to android APK downloads on claim page

v5.3.62
----------
 * Temporarily disable resume imports task

v5.3.61
----------
 * Fix text of save as group dialog
 * Add support to restart export tasks that might have been stopped by deploy

v5.3.60
----------
 * Update to latest mailroom
 * Add urns to runs API endpoint

v5.3.59
----------
 * Update to latest mailroom which returns allow_as_group from query parsing
 * Don't create missing contact fields on flow save

v5.3.57
----------
 * Update flow editor 1.7.16
 * Fix translations on external channel claim page
 * Add tabs to toggle between full flow event history and summary of messages
 * Increase the max height on the flow results export modal dialog

v5.3.56
----------
 * Add params to flow starts API
 * Change name of org_id param in calls to flow/inspect
 * Add quick replies variable to external channel claim page

v5.3.55
----------
 * Allow editing of allow_international on channel update forms
 * Use consistent format for datetimes like created_on on contact list page

v5.3.54
----------
 * Hide loader on start flow dialog when there are no channels

v5.3.53
----------
 * Fix creation of Android channels

v5.3.52
----------
 * Convert Android to dynamic channel type

v5.3.51
----------
 * Update to floweditor 1.7.15
 * Add python script to do all CI required formatting and locale rebuilding
 * Use mailroom for query parsing for contact exports
 * Fix text positioning on list pages
 * Fix delete contact group modal buttons when blocked by dependencies
 * Completion with upper case functions

v5.3.50
----------
 * Migration to set allow_international=true in configs of existing tel channels
 * Remove no longer used flow definition caching stuff

v5.3.49
----------
 * Use realistic phone numbers in mailroom test db
 * Remove contact filtering from flow results page
 * Add migration to populate Flow.template_dependencies

v5.3.48
----------
 * Use mailroom searching for omnibox results

v5.3.47
----------
 * Add template_dependencies m2m

v5.3.46
----------
 * Do not subject requests to the API with sessions to rate limiting
 * Migration to convert flow dependencies metadata to new format
 * Update description on the flow results export to be clear

v5.3.45
----------
 * Fix deletion of orgs and locations so that aliases are properly deleted
 * Remove syntax highlighting in API explorer as it can't handle big responses
 * Use new dependencies format from mailroom

v5.3.44
----------
 * Dynamic group creation / reevaluation through Mailroom

v5.3.43
----------
 * Update to latest mailroom

v5.3.42
----------
 * Fix actions on blocked contact list page

v5.3.41
----------
 * Disable simulation for archived flows
 * Fix query explosion on Android channel alerts

v5.3.40
----------
 * Add subflow parameters to editor

v5.3.39
----------
 * Rework migration code so new flows are migrated too

v5.3.38
----------
 * Use mailroom for contact searches, contact list pages and flow starts via search

v5.3.35
----------
 * Rebuild components

v5.3.34
----------
 * Update to flow editor 1.7.13
 * Don't include 'version' in current definitions
 * Migrate imports of flows to new spec by default

v5.3.30
----------
 * Exclude inactive template translations from API endpoint

v5.3.29
----------
 * Fix edge case for default alias dialog
 * Add sending back to contact list page
 * Save parent result refs in flow metadata
 * Change name BotHub to Bothub

v5.3.28
----------
 * remove auto-now on modified_on on FlowRun

v5.3.27
----------
 * Update to floweditor 1.7.9
 * Warn users if starting for facebook without a topic

v5.3.26
----------
 * Allow arbitrary numbers when sending messages
 * Componentized message sending

v5.3.25
----------
 * Show empty message list if we have archived them all
 * Update to flow editior 1.7.8
 * Replace flow/validate call to mailroom with flow/inspect
 * Add facebook topic selection

v5.3.24
----------
 * Pass version to mailroom migrate endpoint
 * Fix saving on alias editor
 * Support the whatsapp templates HEADER and FOOTER components
 * Write HTTP log for errors in connection

v5.3.23
----------
 * Add support for whatsapp templates with headers and footers
 * Make sure we have one posterizer form and we bind one click event handler for posterize links

v5.3.22
----------
 * Convert add/edit campaign event to components

v5.3.21
----------
 * Add UI for managing globals

v5.3.16
----------
 * Update to flow editor v1.7.7

v5.3.13
----------
 * Update to floweditor v1.7.5
 * Re-add msg_console management command with new support for mailroom
 * Cleanup somes usages of trans/blocktrans

v5.3.12
----------
 * Add error and failure events to contact history
 * Use form components on campaign create/update

v5.3.11
----------
 * Migrate sample flows to new editor
 * Localize URNs in API using org country
 * Write HTTPLogs for Whatsapp template syncing
 * Remove Broadcast recipient_count field

v5.3.10
----------
 * Add read API endpoint for globals

v5.3.9
----------
 * Add trimming task for flow revisions
 * Add models for globals support
 * Add FreshChat channel support

v5.3.8
----------
 * Make sure imported flows are unarchived
 * Validate we do not have a caller on a channel before adding a new one

v5.3.7
----------
 * Release URNs on Org release

v5.3.6
----------
 * Release Channel sync events and alarms

v5.3.5
----------
 * release Campaigns when releasing Orgs

v5.3.4
----------
 * Release flow starts when releasing flows

v5.3.3
----------
 * Add releasing to Classifiers and HTTPLogs

v5.3.2
----------
 * Allow manual syncing of classifiers

v5.3.1
----------
 * Update documentation for FB webhook events to subscribe to

v5.3.0
----------
 * Fix DT One branding and add new icon
 * Fix validation problem on update schedule trigger form
 * Use brand when granting orgs, not host
 * Update contactsql parser to support same quotes escaping as goflow

v5.2.6
----------
 * Change slug for Bothub classifier to 'bothub'

v5.2.5
----------
 * Fix various Schedule trigger UI validation errors
 * Fix intermittently failing excel export tests
 * Add noop reverse in migration

v5.2.1
----------
 * Fix order of Schedule migrations (thanks @matmsa27)

v5.2.0
----------
 * Show date for broadcast schedules
 * Honor initial datetime on trigger schedule ui

v5.1.64
----------
 * Update to flow editor version 1.7.3
 * Fix weekly buttons resetting on trigger schedule form validation
 * Validate schedule details on schedule trigger form
 * Show query editors in contact search
 * Add migration to fix schedules with None/NaN repeat_days_of_week values
 * Move IE9 shim into the main template header
 * Update README with final 5.0 versions

v5.1.63
----------
 * Update to flow editor v1.7.2

v5.1.62
----------
 * Validate repeat_days_of_week when updating schedules
 * Include airtime transfers in contact history

v5.1.61
----------
 * Tweak styling on contact field list page
 * Send test email when the SMTP server config are set

v5.1.60
----------
 * Add Bothub classifier type

v5.1.59
----------
 * Update flow editor to version 1.7.0
 * Add Split by Intent action in flows
 * Update Send Airtime action for use with DTOne

v5.1.58
----------
 * Unify max contact fields
 * Don't allow deletion of flow labels with children
 * Rename TransferTo to DTOne

v5.1.57
----------
 * Check pg_dump version when creating dumps
 * Add missing block super in extra script blocks
 * Fix omnibox being not actually required on send message form
 * Rework airtime transfers to have separate http logs
 * Allow flow starts by query

v5.1.55
----------
 * Sync intents on classifier creation
 * Trim HTTP logs older than 3 days

v5.1.54
----------
 * remove fragile AT links to configuration pages
 * Exclude hidden results from flow results page
 * Exclude results with names starting with _ from exports

v5.1.53
----------
 * Classifier models and views
 * HTTPLog models and views

v5.1.52
----------
 * add prefetch to retry

v5.1.51
----------
 * Add ThinQ Channel Type

v5.1.50
----------
 * Fix contact history rendering of broadcast messages with null recipient count
 * Fix for start_session action in the editor

v5.1.49
----------
 * Fire schedules in Mailroom instead of celery

v5.1.48
----------
 * Rework contact history to include engine events

v5.1.47
----------
 * Update to flow editor 1.6.20

v5.1.46
----------
 * Rev Flow Editor v1.6.19

v5.1.45
----------
 * Fix rendering of campaigns on export page
 * Fix ivr channel logs
 * Make FlowRun.status non-NULL
 * Make FlowSession.uuid unique and indexed

v5.1.44
----------
 * Tidy up fields on flow activity models
 

v5.1.43
----------
 * Fix styling on create flow dialog
 * Make user fields nullable on broadcasts
 * Populate repeat_minute_of_hour in data migration

v5.1.42
----------
 * Update trigger update views to take into account new schedule fields

v5.1.41
----------
 * Update docs on flow start extra to be accessible via @trigger
 * Change input selector to work cross-browser on send modal
 * Don't inner scroll for modax fetches

v5.1.40
----------
 * Fix issues with web components in Microsoft Edge

v5.1.37
----------
 * Cleanup Schedule class
 * Drop unused columns on FlowRun
 * Remove legacy engine code
 * Remove legacy braodcast and message sending code

v5.1.36
----------
 * Temporarily disable compression for components JS

v5.1.33
----------
 * Use new expressions for campaign message events, broadcasts and join group triggers
 * List contact fields with new expression syntax and fix how campaign dependencies are rendered

v5.1.28
----------
 * Use mailroom to interrupt runs when archiving or releasing a flow
 * Re-organize legacy engine code
 * Initial library of web components

v5.1.27
----------
 * Update to floweditor 1.6.13
 * Allow viewers to do GETs on some API endpoints

v5.1.26
----------
 * Fix rendering of campaign and event names in UI
 * Move remaining channel client functionality into channel type packages
 * Remove unused asset server stuff

v5.1.25
----------
 * Update floweditor to 1.6.12
 * Allow viewing of channel logs in anonymous orgs with URN values redacted

v5.1.24
----------
 * Cleanup campaighn models fields

v5.1.23
----------
 * Really fix copying of flows with nameless has_group tests and add a test this time

v5.1.22
----------
 * Remove trigger firing functionality (except schedule triggers) and drop unused fields on trigger

v5.1.21
----------
 * Migration to backfill FlowRun.status

v5.1.20
----------
 * Limit group fetching to active groups
 * Get rid of caching on org object as that's no longer used needed
 * Fix importing/copying flows when flow has group dependency with no name

v5.1.19
----------
 * Migration to add FlowRun.status

v5.1.18
----------
 * Cleanup fields on FlowRun (single migration with no real SQL changes which can be faked)

v5.1.17
----------
 * Remove all IVR flow running functionality which is now handled by mailroom

v5.1.15
----------
 * Update to flow editor v1.6.11
 * Releasing Nexmo channel shouldn't blow up if application can't be deleted on Nexmo side

v5.1.14
----------
 * Fix Nexmo IVR to work with mailroom
 * Add migration to populate session UUIDs
 * Update to Django 2.2
 * Send topup expiration emails to all org administrators

v5.1.12
----------
 * Drop ActionLog model
 * Switch to new editor as the default, use v1.6.10
 * Add query field to FlowStart

v5.1.11
----------
 * Add FlowSession.uuid which is nullable for now
 * Update to floweditor 1.6.9, scrolling rules

v5.1.10
----------
 * Update to flow editor 1.6.8, add completion config
 * Add FlowStart.parent_summary, start deprecating fields
 * Switch to bionic beaver for CI builds
 * Add trigger params access to ivr flow
 * Drop no longer used Broadcast.purged field

v5.1.9
----------
 * Make Broadcast.purged nullable in preparation for dropping it

v5.1.8
----------
 * Update floweditor to 1.6.7 and npm audit

v5.1.7
----------
 * Remove unused IVR tasks
 * Simplify failed IVR call handling

v5.1.6
----------
 * Fix format_number to be able to handle decimals with more digits than current context precision

v5.1.5
----------
 * Update to flow editor 1.6.6

v5.1.4
----------
 * Update to flow editor 1.6.5
 * Update Django to 2.1.10

v5.1.3
----------
 * Update flow editor to 1.6.3

v5.1.2
----------
 * Remove fields no longer needed by new engine
 * Trim sync events in a separate task

v5.1.1
----------
 * Stop writing legacy engine fields and make them nullable
 * Remove no longer used send_broadcast_task and other unused sending code
 * Squash migrations into previously added dummy migrations

v5.1.0
----------
 * Populate account sid and and auth token on twilio callers when added
 * Disable legacy IVR tasks

v5.0.9
----------
 * Add dummy migrations for all migrations to be created by squashing

v5.0.8
----------
 * Update recommended versions in README
 * Fix API runs serializer when run doesn't have category (i.e. from save_run_result action)
 * Update to latest floweditor
 * Update search parser to convert timestamps into UTC

v5.0.7
----------
 * Force a save when migrating flows

v5.0.6
----------
 * Show search error if input is not a date
 * Group being imported into should be in state=INITIALIZING whilist being populated, and hide such groups in the UI
 * Only add initially changed files in post-commit hook
 * Fix to make sure the initial form data is properly shown on signup

v5.0.5
----------
 * sync whatsapp templates with unsupported languages, show them as such

v5.0.4
----------
 * Update to floweditor v1.5.15
 * Add pagination to outbox
 * Fix import of contact field when field exists with same name but different key
 * Fix (old) mac excel dates in imports

v5.0.3
----------
 * Update flow editor to 1.5.14

v5.0.2
----------
 * Remove reference to webhook API page which no longer exists
 * Update to flow-editor 1.5.12
 * Update some LS libs for security
 * Tweaks to migrate_to_version_11_1 to handle "base" as a lang key
 * Tweak old flow migrations to allow missing webhook_action and null ruleset labels

v5.0.1
----------
 * Fix max length for WA claim facebook_access_token
 * Fix WhatsApp number formatting on contact page, add icon

v5.0.0
----------
 * add validation of localized messages to Travis

v4.27.3
----------
 * Make contact.is_test nullable
 * Migration to remove orphaned schedules and changes to prevent creating them in future
 * Migration to merge path counts from rules which are merged into a single exit in new engine

v4.27.2
----------
 * fix broadcast API test

v4.27.1
----------
 * temporarily increase throttling on broadcasts endpoint

v4.27.0
----------
 * Cleanup webhook fields left on Org
 * Stop checking flow_server_enabled and remove support for editing it

v4.26.1
----------
 * Remove no longer used check_campaigns_task

v4.26.0
----------
 * Remove handling of incoming messages, channel events and campaigns.. all of which is now handled by mailroom

v4.25.0
----------
 * Add sentry error to handle_event_task as it shouldnt be handling anything
 * Remove processing of timeouts which is now handled by mailroom
 * Start broadcast mailroom tasks with HIGH_PRIORITY
 * Fix EX settings page load
 * Migration to convert any remaining orgs to use mailroom
 * Fix broken links to webhook docs
 * Simplify WebHookEvent model

v4.23.3
----------
 * Send broadcasts through mailroom
 * Add org name in the email subject for exports
 * Add org name in export filename

v4.24.0
----------
 * Add org name in the export email subject and filename
 * Update flow editor to 1.5.9
 * Remove functionality for handling legacy surveyor submissions

v4.23.1
----------
 * Make exported fields match goflow representation and add .as_export_ref() to exportable classes
 * Update to latest floweditor v1.5.5
 * Persist group and field definitions in exports
 * Add support for SignalWire (https://signalwire.com) for SMS and IVR

v4.23.0
----------
 * Save channel and message label dependencies on flows

v4.22.63
----------
 * Update to latest floweditor v1.5.5
 * Allow switching between editors
 * Update Django to version 2.1.9

v4.22.62
----------
 * add US/ timezones for clicksend as well

v4.22.61
----------
 * add clicksend channel type

v4.22.60
----------
 * Update flow editor to 1.5.4
 * Allow imports and exports of v13 flows

v4.22.55
----------
 * Enable export of new flows
 * Update Nexmo supported countries list

v4.22.54
----------
 * rename migration, better printing

v4.22.53
----------
 * add migration to repopulate metadata for all flows

v4.22.52
----------
 * Expose result specs in flow metadata on flows API endpoint
 * Use Temba JSON adapter when reading JSON data from DB
 * Don't update TwiML channel when claiming it
 * Use most recent topup for credit transfers between orgs

v4.22.51
----------
 * Update to flow-editor 1.5.3

v4.22.50
----------
 * Update to floweditor v1.5.2

v4.22.49
----------
 * Only do mailroom validation on new flows

v4.22.48
----------
 * Fix 11.12 migration and importing flows when flow contains a reference to a channel in a different org
 * Make WhatsApp endpoint configurable, either FB or self-hosted

v4.22.47
----------
 * tweak to WA language mapping

v4.22.46
----------
 * add hormuud channel type
 * newest editor
 * update invitation secret when user is re-invited

v4.22.45
----------
 * Tweak compress for vendor

v4.22.44
----------
 * Update to flow editor 1.4.18
 * Add mailroom endpoints for functions, tweak styles for selection
 * Honor is_active when creating contact fields
 * Cache busting for flow editor

v4.22.43
----------
 * Update flow editor to 1.4.17
 * Warn users when starting a flow when they have a WhatsApp channel that they should use templates

v4.22.42
----------
 * add page to view synched WhatsApp templates for a channel

v4.22.41
----------
 * Update flow editor to 1.4.16
 * View absolute attachments in old editor

v4.22.40
----------
 * Update editor to 1.4.14

v4.22.39
----------
 * latest editor

v4.22.38
----------
 * update defs with db values both when writing and reading
 * remove clearing of external ids for messages

v4.22.37
----------
 * Update to flow-editor 1.4.12
 * Remove footer gap on new editor

v4.22.36
----------
 * allow Alpha users to build flows in new editor
 * don't use RuleSets in figuring results, exports, categories

v4.22.28
----------
 * Adjust `!=` search operator to include unset data
 * Remove broadcast recipients table
 * IMPORTANT * You must make sure that all purged broadcasts have been archived using
   rp-archiver v1.0.2 before deploying this version of RapidPro

v4.22.27
----------
 * styling tweaks to contacts page

v4.22.26
----------
 * Always show featured ContactFields on Contact.read page
 * Do not migrate ruleset with label null and action msg text null

v4.22.25
----------
 * only show pagination warning when we have more than 10k results

v4.22.24
----------
 * support != search operator

v4.22.23
----------
 * simplify squashing of squashable models
 * show a notification when users open the last page of the search
 * update `modified_on` once msgs export is finished

v4.22.22
----------
 * Fix issue with pagination when editing custom fields

v4.22.21
----------
 * Add new page for contact field management

v4.22.20
----------
 * add management command to reactivate fb channels

v4.22.19
----------
 * api for templates, add access token and fb user id to claim, sync with facebook endpoint

v4.22.18
----------
 * fix recalculating event fires for fields when that field is created_on

v4.22.17
----------
 * Don't overwrite show_in_table flag on contact import
 * Prevent updates of contact field labels when adding a field to a flow
 * Add migration to populate results and waiting_exit_uuids in Flow.metadata

v4.22.15
----------
 * Do not immediately expire flow when updating expirations (leave that to mailroom)
 * Fix boundary aliases duplicates creation
 * Add org lock for users to deal with similtaneous updates of org users
 * Add results and waiting_exit_uuids to flow metadata and start populating on Flow.update

v4.22.14
----------
 * CreateSubOrg needs to be non-atomic as well as it creates flows which need to be validated
 * Remove unused download view

v4.22.13
----------
 * allow blank pack, update permissions

v4.22.12
----------
 * remove APK read view, only have update
 * allow setting pack number

v4.22.11
----------
 * Add APK app and new Android claiming pipeline for Android Relayer

v4.22.10
----------
 * Use output of flow validation in mailroom to set flow dependencies
 * Make message_actions.json API endpoint support partial updates
 * Log to librato only pending messages older than a minute

v4.22.6
----------
 * Add Viber Welcome Message event type and config
 * More customer support service buttons

v4.22.5
----------
 * queue incoming messages and incoming calls from relayer to mailroom

v4.22.4
----------
 * Temporarily disable flow validation until we can fix it for new orgs

v4.22.3
----------
 * Lazily create any dependent objects when we save
 * MAILROOM_URL in settings.py.dev should default to http://localhost:8090
 * Call to mailroom to validate a flow before saving a new definition (and fix invalid flows in our tests)

v4.22.2
----------
 * Fix schedule next fire calculation bug when schedule is greater than number of days
 * Fix to allow archiving flow for removed(inactive) campaign events
 * Strip resthook slug during creation
 * Ignore request from old android clients using GCM

v4.22.1
----------
 * Increase the schedule broadcast text max length to be consistent on the form

v4.22.0
----------
 * Fix case of single node flow with invalid channel reference
 * Remove ChannelConnection.created_by and ChannelConnection.is_active
 * Fix flow export results to include results from replaced rulesets

v4.21.15
----------
 * correct exclusion

v4.21.14
----------
 * Dont requeue flow server enabled msgs
 * Exit sessions in bulk exit, ignore mailroom flow starts

v4.21.13
----------
 * Fix import with invalid channel reference
 * Add flow migration to remove actions with invalid channel reference

v4.21.12
----------
 * improve simulator for goflow simulation

v4.21.11
----------
 * work around JS split to show simulator images

v4.21.10
----------
 * display attachments that are just 'image:'

v4.21.9
----------
 * simulator tweaks
 * show Django warning if mailroom URL not configured

v4.21.8
----------
 * make sure we save flow_server_enabled in initialize

v4.21.7
----------
 * Update status demo view to match the current webhook posted data
 * Remove all remaining reads of contact.is_test

v4.21.6
----------
 * Use pretty datetime on contact page for upcoming events

v4.21.5
----------
 * Replace final index which references contact.is_test
 * Fix labels remap on flow import

v4.21.4
----------
 * All new orgs flow server enabled
 * Fallback to org domain when no channe domain set

v4.21.3
----------
 * Remove all remaining checks of is_test, except where used in queries
 * Update contact indexes to not include is_test
 * Prevent users from updating dynamic groups if query is invalid
 * Update Python module dependencies

v4.21.2
----------
 * set country code on test channel

v4.21.1
----------
 * do not log errors for more common exceptions

v4.21.0
----------
 * Include fake channel asset when simulating
 * Add test for event retrying, fix out of date model
 * Stop checking contact.is_test in db triggers

v4.20.1
----------
 * Remove unused fields on webhookevent
 * Default page title when contact has no name or URN (e.g. a surveyor contact)

v4.19.7
----------
 * fix simulator to allow fields with empty value
 * remove remaining usages of test contacts for testing

v4.19.6
----------
 * add incoming_extra flow to mailroom test
 * fix for test contact deletion migration

v4.19.5
----------
 * pass extra to mailroom start task

v4.19.4
----------
 * Support audio/mp4 as playable audio
 * Add migration to remove test contacts

v4.19.3
----------
 * Ensure scheduled triggers start flows in mailroom if enabled

v4.19.2
----------
 * remap incoming ivr endpoints for Twilio channels when enabling flow server
 * interrupt flow runs when enabling flow server
 * add enable_flow_server method to org, call in org update view

v4.19.1
----------
 * Scope API throttling by org and user
 * Add export link on campaign read page
 * Fix SMTP serever config to percentage encode slashes

v4.19.0
----------
 * Add session_type field on FlowSession
 * Use provided flow definition when simulating if provided
 * Remove USSD app completely
 * Adjust broadcast status to API endpoint
 * Remove legacy (non-mailroom) simulation

v4.18.0
----------
 * Make ChannelConnection.is_active nullable so it can be eventually removed
 * Replace traceback.print_exc() with logger.error
 * Make sure contacts ids are iterable when starting a flow
 * Remove USSD proxy model

v4.17.0
----------
 * Use URL kwargs for channel logs list to pass the channel uuid
 * Fix message campaign events on normal flows not being skipped
 * Default to month first date format for US timezones
 * Make Contact.created_by nullable
 * Fix to prevent campaign event to create empty translations
 * Use new editor wrapper to embed instead of building
 * Remove USSD functionality from engine

v4.16.15
----------
 * Fix Stripe integration

v4.16.14
----------
 * fix webhook bodies to be json

v4.16.13
----------
 * better request logging for webhook results

v4.16.12
----------
 * further simplication of webhook result model, add new read and list pages

v4.16.11
----------
 * add org field to webhook results

v4.16.10
----------
 * Add surveyor content in mailroom_db command
 * Fix flows with missing flow_type
 * Update more Python dependencies
 * Prevent flows of one modality from starting subflows of a different modality

v4.16.8
----------
 * Add support for Movile/Wavy channels
 * Switch to codecov for code coverage
 * Allow overriding brand domain via env
 * Add mailroom_db management command for mailroom tests
 * Start flow_server_enabled ivr flows in mailroom
 * Remove legacty channel sending code
 * Remove flow dependencies when deactivating USSD flows
 * Migrations to deactivate USSD content

v4.16.5
----------
 * Fix quick replies in simulator

v4.16.4
----------
 * More teaks to Bongolive channel
 * Use mailroom simulation for IVR and Surveyor flows
 * Add a way to see all run on flow results runs table

v4.16.3
----------
 * Simplify generation of upload URLs with new STORAGE_URL setting

v4.16.2
----------
 * Switch BL channels used API
 * Fix rendering of attachments for mailroom simulation
 * Update black to the version 18.9b0

v4.16.0
----------
 * Fix flow_entered event name in simulator
 * Make created_by, modified_by on FlowStart nullable, add connections M2M on FlowStart
 * Rename ChannelSession to ChannelConnection

v4.15.2
----------
 * Fix for flow dependency migration
 * Fix rendering of single digit hours in pretty_datetime tag
 * Use mailroom for flow migration instead of goflow
 * Add support for Bongo Live channel type

v4.15.1
----------
 * Include default country in serialized environments used for simulation
 * Add short_datetime and pretty_datetime tags which format based on org settings
 * Prevent users from choosing flow they are editing in some cases

v4.15.0
----------
 * Fix nexmo claim
 * Tweak 11.7 migration to not blow up if webhook action has empty URL
 * Bump module minor versions and remove unused modules
 * Remove ChannelSession.modified_by

v4.14.1
----------
 * Make older flow migrations more fault tolerant
 * Tweaks to migrate_flows command to make error reporting more useful
 * Add flow migration to fix duplicate rule UUIDs
 * Update python-telegram-bot to 11.1.0
 * Update nexmo to 2.3.0

v4.14.0
----------
 * Fix recent messages rollover with 0 messages
 * Use flowserver only for flow migration
 * Make created_by and modified_by optional on channel session

v4.13.2
----------
 * create empty revisions for empty flows
 * proper handle of empty errors on index page
 * fix error for policy read URL failing
 * add quick replies to mailroom simulator

v4.13.1
----------
 * populate simulator environment for triggers and resumes
 * honour Flow.is_active on the Web view
 * fix android channel release to not throw if no FCM ID
 * add Play Mobile aggregator

v4.13.0
----------
 * Add index for fast Android channel fetch by last seen
 * Remove gcm_id field
 * No messages sheet for flow results export on anon orgs
 * Add periodic task to sync channels we have not seen for a while
 * Add wait_started_on field to flow session

v4.12.6
----------
 * Remove flow server trialling
 * Replace tab characters for GSM7
 * Use mailroom on messaging flows for simulation
 * Raise ValidationError for ContactFields with null chars
 * upgrade to Django 2.1

v4.12.5
----------
 * Make sure Flow.update clears prefetched nodes after potentialy deleting them

v4.12.4
----------
 * Fix Flow.update not deleting nodes properly when they change type

v4.12.3
----------
 * Add try/except block on FCM sync
 * Issue #828, remove numbers replace

v4.12.2
----------
 * Dont show queued scheduled broadcasts in outbox
 * Prevent deleting groups with active campaigns
 * Activate support for media attachment for Twitter channels
 * Remove ability to create webhook actions in editor
 * Add flow migration to replace webhook actions with rulesets

v4.12.1
----------
 * Fix importing campaign events based on created_om
 * Fix event fires creation for immutable fields
 * Remove WA status endpoint
 * Fix IVR runs expiration date initialization
 * Add UUID field to org

v4.11.7
----------
 * Interrupt old IVR calls and related flow sessions
 * Move webhook docs button from the token view to the webhook view

v4.11.6
----------
 * Faster squashing
 * Fix EX bulk sender form fields

v4.11.5
----------
 * simulate flow_server_enabled flows in mailroom

v4.11.3
----------
 * Add session log links to contact history for staff users
 * Hide old webhook config page if not yet set

v4.11.2
----------
 * Fix passing false/true to archived param of flows API endpoint

v4.11.1
----------
 * Turn on the attachment support for VP channels
 * Tweak 11.6 flow migration so that we remap groups, but never create them
 * Flows API endpoint should support filtering by archived and type
 * Log how many flow sessions are deleted and the time taken
 * Turn on the attachment support for WA channels
 * Adjust UI for adding quick replies and attachment in random order

v4.11.0
----------
 * Add index for fetching waiting sessions by contact
 * Ensure test_db users have same username and email
 * Add index to FlowSession.ended_on
 * Make FlowSession.created_on non-null
 * Add warning class to skipped campaigns event fire on contact history
 * Add fired_result field to campaign event fires

v4.10.9
----------
 * Log and fail calls that cannot be started
 * Allow contact.created_on in flows, init new event

v4.10.8
----------
 * Deactivate events when updating campaigns
 * Less aggressive event fire recreation
 * Use SMTP SERVER org config and migrate old config keys

v4.10.4
----------
 * Retry failed IVR calls

v4.10.3
----------
 * Show all split types on run results, use elastic for searching

v4.10.2
----------
 * Flow migration for mismatched group uuids in existing flows
 * Remap group uuids on flow import
 * Migration to backfill FlowSession.created_on / ended_on

v4.10.1
----------
 * Add config to specify content that should be present in the response of the request, if not mark that as msg failed
 * Allow campaign events to be skipped if contacts already active in flows

v4.10.0
----------
 * Add FlowRun.parent_uuid
 * Add FlowSession.timeout_on
 * Create new flows with flow_server_enabled when org is enabled
 * Add flow-server-enabled to org, dont deal with flow server enabled timeouts or expirations on rapidpro

v4.9.2
----------
 * Fix flowserver resume tests by including modified_on on runs sent to goflow

v4.9.1
----------
 * Dont set preferred channels if they can't send or call
 * Don't assume events from goflow have step_uuid
 * Add indexes for flow node and category count squashing

v4.9.0
----------
 * Delete event fires in bulk for inactive events
 * Fix using contact language for categories when it's not a valid org language
 * Fix translation of quick replies
 * Add FlowSession.current_flow and start populating
 * Refresh contacts list page after managing fields
 * Update to latest goflow (no more caller events, resumes, etc)
 * Fix flow results export to read old archive format
 * Batch event fires by event ID and not by flow ID
 * Make campaign events immutable

v4.8.1
----------
 * Add novo channel

v4.8.0
----------
 * Remove trialing of campaign events
 * Remove no longer used ruleset_analytis.haml
 * Expose @contact.created_on in expressions
 * Make Contact.modified_by nullable and stop writing to it
 * Optimize group releases
 * Add created_on/ended_on to FlowSession

v4.7.0
----------
 * Bump Smartmin and Django versions
 * Expose @contact.created_on in expressions
 * Make Contact.modified_by nullable and stop writing to it

v4.6.0
----------
 * Latest goflow

v4.5.2
----------
 * Add config for deduping messages
 * Add created_on/ended_on to FlowSession
 * Update to latest goflow (event changes)
 * Do not delete campaign events, deactivate them
 * Do not delete runs when deleting a flow
 * Fix Campaigns events delete for system flow

v4.5.1
----------
 * Use constants for queue names and switch single contact flow starts to use the handler queue
 * Raise ValidationError if flow.extra is not a valid JSON
 * Defer group.release in a background task
 * Fix saving dynamic groups by reverting back to escapejs for contact group query on dialog

v4.5.0
----------
 * Add Stopped event to message history and unknown/unsupported events
 * Switch result value to be status code from webhook rulesets, save body as @extra.<resultname> and migrate result references to that

v4.4.20
----------
 * Fix channel selection for sending to TEL_SCHEME
 * Add campaigns to all test orgs for make_db
 * Correctly embed JS in templates
 * Escape data before using `mark_safe`

v4.4.19
----------
 * Fix validating URNField when input isn't a string

v4.4.18
----------
 * Fix incorrect units in wehbook_stats
 * Result input should always be a string

v4.4.17
----------
 * Don't do duplicate message check for surveyor messages which are already SENT
 * Update to goflow 0.15.1
 * Update Location URLs to work with GADM IDs
 * Fix potential XSS issue: embed script only if `View.refresh` is set

v4.4.16
----------
 * Fix IVR simulation

v4.4.15
----------
 * Fix importing with Created On columns
 * Validate URNs during import
 * Classify flow server trials as simple if they don't have subflows etc
 * Use latest goflow for testing

v4.4.14
----------
 * Enable import of GADM data using import_geojson

v4.4.13
----------
 * Defer to mailroom for processing event fires for flows that are flowserver enabled
 * Tweaks to comparing events during flow server trials
 * Fix saved operand for group tests on anon orgs

v4.4.12
----------
 * Add step URN editor completions
 * Add name to the channels shown on the flow editor
 * Don't zero pad anon ids in context
 * Update to latest expressions

v4.4.11
----------
 * Ensure API v1 writes are atomic
 * JSONFields should use our JSON encoder
 * Use authenticated user for events on Org.signup
 * Trial shouldn't blow up if run has no events
 * Add urn to step/message context and make urn scheme accessible for anon org
 * Get rid of Flow.FLOW

v4.4.8
----------
 * Don't trial flow starts from triggers
 * Fix messages from non-interactive subflows being added to their parent run
 * Setup user tracking before creating an Org
 * Migrate flows during flowserver trials with collapse_exits=false to keep paths exactly the same
 * Input for a webhook result test should be a single request
 * Migration to update F type flows to M

v4.4.7
----------
 * Enforce validation on OrgSignup and OrgGrant forms
 * Cleanup encoding of datetimes in JSON
 * New flows should be created with type M and rename constants for clarity

v4.4.6
----------
 * Fix updating dynamic groups on contact update from the UI
 * Make editor agnostic to F/M flow types

v4.4.5
----------
 * Remove mage functionality
 * Fix Twilio number searching

v4.4.2
----------
 * Use SystemContactFields for Dynamic Groups
 * Add our own json module for loads, dumps, always preserve decimals and ordering
 * Replace reads of Flow.flow_type=MESSAGE with Flow.is_system=True
 * Migration to populate Flow.is_system based on flow_type

v4.4.0
----------
 * Fix intercom ResourceNotFound on Org.Signup
 * Remove follow triggers and channel events
 * Add Flow.is_system and start populating for new campaign event single message flows

v4.3.8
----------
 * Data migration to deactivate all old style Twitter channels
 * Update Nexmo client

v4.3.4
----------
 * Increase IVR logging verbosity
 * Trial all campaign message flows in flowserver
 * Tweak android recommendation

v4.3.3
----------
 * Run Table should only exclude the referenced run, and include greater Ids
 * Raise validation error ehen trying action inactive contacts over API
 * Remove uservoice as a dependency
 * Update versions of Celery, Postgis, Nexmo, Twilio
 * Fix Python 3.7 issues
 * Clear out archive org directory when full releasing orgs

v4.3.2
----------
 * Update expressions library to get EPOCH() function

v4.3.1
----------
 * Update to Django 2.0
 * Update postgres adapter to use psycopg2-binary

v4.3.0
----------
 * Wrap asset responses in a results object
 * Use trigger type of campaign when starting campign event flows in flowserver
 * Fix count for blocktrans to not use string from intcomma
 * Use audio/mp4 content type for m4a files

v4.2.4
----------
 * Update to latest goflow and enable asset caching
 * Actually fix uploading mp4 files

v4.2.2
----------
 * Show only user fields when updating field values for a contact
 * Fix MIME type for M4A files
 * Allow test_db command to work without having ES installed

v4.2.1
----------
 * Ignore search exceptions in omnibox
 * Actually enable users to use system contact fields in campaign events

v4.2.0
----------
 * Enable users to choose 'system fields' like created_on for campaign events

v4.1.0
----------
 * Management commnd to recalculate node counts
 * Fix run path triggers when paths are trimmed
 * Allow file overwrite for public S3 uploads

v4.0.3
----------
 * Handle cases when surveyor submits run with deleted action set
 * Document modified_on on our API endpoint
 * Use ElasticSearch for the omnibox widget

v4.0.2
----------
 * fix count of suborgs after org deletion

v4.0.1
----------
 * remove group settings call for WhatsApp which is no longer supported
 * easier way to service flows for CS reps

v4.0.0
----------
 * Squash all migrations

v3.0.1000
----------
 * fix display of archives formax on home page

v3.0.999
----------
 * Fix chatbase font icon name
 * Add encoding config to EX channel type
 * Show archive link and information on org page

v3.0.449
----------
 * Improve error message when saving surveyor run fails
 * Allow surveyor submissions to match rules on old revisions
 * Fix bug in msg export from archives

v3.0.448
----------
 * Support audio attachments in all the audio formats that we can play
 * Add name and input to runs API v2 endpoint
 * Update InGroup test to match latest goflow
 * Expose resthooks over the assets endpoint and update logic to match new engine
 * Support messages export from archives

v3.0.447
----------
 * Configure Celery to discover Wechat and Whatsapp tasks
 * Add Rwanda and Nigeria to AT claim form options
 * Extend timeout for archives links to 24h
 * Add created_on to the contact export

v3.0.446
----------
 * Use constants for max contact fields and max group membership columns
 * Tweaks to twitter activity claiming that deals with webhooks already being claimed, shows errors etc
 * Rename form field to be consistent with the constants we use
 * Writes only now use XLSLite, more coverage
 * Limit number of groups for group memberships in results exports
 * Swicth message export to use XLSLite
 * Fix default ACL value for S3 files
 * Add WeChat (for beta users)

v3.0.445
----------
 * fix dupe sends in broadcast action

v3.0.444
----------
 * fix per credit calculation

v3.0.443
----------
 * two decimals for per credit costs, remove trailing 0s

v3.0.442
----------
 * Fix ContactField priority on filtered groups
 * Update Django to version 1.11.14
 * Reenable group broadcasts

v3.0.438
----------
 * When comparsing msg events in flowserver trials, make paths relative again
 * Change VariableContactAction to create contacts even without URNs
 * Fix import of ID columns from anon export
 * Don't fail twilio channel releases if auth key is no longer vaild
 * Add UI messaging for archived data

v3.0.437
----------
 * Fix import of header ID from anon export

v3.0.436
----------
 * Fix supported scheme display lookup
 * Move action log delete to flow run release

v3.0.435
----------
 * Fix group test operand when contact name is null
 * Mention all AfricasTalking countries on claim page
 * Warn user of columns to remove on import
 * Release events properly on campaign import
 * Add languages endpoint to asset server

v3.0.434
----------
 * Add option for two day run expiration
 * Change group rulesets to use contact as operand same as new engine
 * Fix reconstructing sessions for runs being trialled in the flowserver so that we include all session runs

v3.0.433
----------
 * Write boolean natively when exporting to xlsx
 * Improve reporting of flow server errors during trials
 * Clarify about contact import columns
 * Update flow result exports to match recent changes to contact exports

v3.0.432
----------
 * Update modified_on on contacts that have their URN stolen
 * Full releasing of orgs and users

v3.0.431
----------
 * Set exit_uuid at end of path when run completes
 * Make twitter activity API the default twitter channel type
 * Add Nigeria and Rwanda to AT supported countries
 * Don't exclude result input from flowserver trial result comparisons
 * Use operand rather than msg text for result input
 * Remove reporting to sentry when @flow.foo.text doesn't equal @step.text
 * Add flow migration to replace @flow.foo.text expressions on non-waiting rulesets

v3.0.430
----------
 * Fix message flow updating

v3.0.429
----------
 * Remove org.is_purgeable
 * Fix format of archived run json to match latest rp-archiver
 * Fix checking of result.text values in the context
 * Import/Export column headers with type prefixes
 * Add groups membership to contacts exports
 * Retry calls that are in IVRCall.RETRY_CALL
 * Retry IVR outgoing calls if contact did not answer

v3.0.428
----------
 * Add FlowRun.modified_on to results exports
 * Change how we select archives for use in run exports to avoid race conditions
 * Report to sentry when @flow.foo.text doesn't match @step.text

v3.0.427
----------
 * Release webhook events on run release
 * Fetch run results from archives when exporting results
 * Don't create action logs for non-test contacts

v3.0.426
----------
 * Migrations for FK protects, including all SmartModels
 * Update to latest xlsxlite to fix exporting date fields
 * Remove  merged runs sheet from results exports
 * Modified the key used in the transferto API call

v3.0.425
----------
 * Enable burst sms type

v3.0.424
----------
 * add burst sms channel type (Australia and New Zealand)

v3.0.423
----------
 * trim event fires every 15 minutes

v3.0.422
----------
 * Trim event fires older than a certain age
 * More consistent name of date field on archive model
 * Remove no longer needed functionality for runs that don't have child_context/parent_context set

v3.0.421
----------
 * Degroup contacts on deactivate

v3.0.420
----------
 * release sessions on reclaimed urns

v3.0.419
----------
 * special case deleted scheme in urn parsing
 * release urn messages when releasing a contact
 * add delete reason to run

v3.0.418
----------
 * Clear child run parent reference when releasing parent
 * Make sync events release their alerts
 * Release sessions, anonymize urns

v3.0.417
----------
 * add protect to contacts and flows, you can fake the migrations in this release

v3.0.416
----------
 * add deletion_date, use full path as link name
 * add unique constraint to disallow dupe archives

v3.0.415
----------
 * add needs_deletion field, remove is_purged

v3.0.414
----------
 * Set run.child_context when child has no waits
 * Use latest openpyxl and log the errors to sentry
 * Don't blow up if trialled run has no events
 * Allow editors to see archives / api
 * Migration to backfill run parent_context and child_context

v3.0.412
----------
 * Fix archive filter test
 * Include id when serializing contacts for goflow

v3.0.411
----------
 * Show when build failed becuse black was not executed
 * Fix calculation of low threshold for credits to consider only the top with unused credits
 * All flows with subflows to be trialled in the flowserver
 * Create webhook mocks for use in flowserver trials from webhook results
 * Enable Archive list API endpoint

v3.0.410
----------
 * Remove purging, add release with delete_reason
 * Set parent_context in Flow.start and use it in FlowRun.build_expressions_context if available
 * Add is_archived counts for LabelCounts and SystemLabelCounts, update triggers

v3.0.409
----------
 * Remove explicit use of uservoice
 * Use step_uuids for recent message calculation

v3.0.408
----------
 * Format code with blackify
 * Add management commands to update consent status and org membership
 * Update to latest goflow to fix tests
 * Fix 'raise None' in migration and make flow server trial period be 15 seconds
 * Fix the campaign events fields to be datetime fields
 * Move flow server stuff from utils.goflow to flows.server
 * Add messangi channel type

v3.0.407
----------
 * Reenable requiring policy consent
 * Allow msgs endpoint to return ALL messages for an org sorted by created_on
 * Return error message if non-existent asset requested from assets endpoint
 * If contact sends message whilst being started in a flow, don't blow up
 * Remove option to have a flow never expire, migrate current flows with never to 30 days instead
 * Request the user to fill the LINE channel ID and channel name on the claim form

v3.0.406
----------
 * Fix logging events to intercom

v3.0.405
----------
 * Migration to remove FlowStep

v3.0.404
----------
 * remove old privacy page in favor of new policy app
 * use python3 `super` method
 * migration to backfill step UUIDs on recent runs

v3.0.403
----------
 * tweaks to add_analytics users

v3.0.402
----------
 * add native intercom support, add management command to update all users

v3.0.401
----------
 * Fix quick replies in simulator
 * Lower the min length for Facebook page access token
 * Update Facebook claim to ask for Page ID and Page name from the user
 * Add new policies and consent app
 * Fix another migration that adds a field and writes to it in same transaction
 * Add step UUID fields to FlowPathRecentRun and update trigger on run paths to start populating them

v3.0.400
----------
 * Don't create flow steps
 * Remove remaining usages of six

v3.0.399
----------
 * Drop no longer used FlowRun.message_ids field
 * Don't allow nested flowserver trials
 * Fix migrations which can lead to locks because they add a field and populate it in same transaction
 * Remove a lot of six stuff
 * Use bulk_create's returned msgs instead of forcing created_on to be same for batches of messages created by Broadcast.send
 * Use sent_on for incoming messages's real world time
 * Don't require steps for flow resumptions

v3.0.398
----------
 * Add period, rollup fields to archive

v3.0.397
----------
 * Stop writing .recipients when sending broadcasts as this is only needed for purged broadcasts
 * Rework run_audit command to check JSON fields and not worry about steps
 * Replace json_date_to_datetime with iso8601.parse_date
 * Stepless surveyor runs

v3.0.396
----------
 * Use run path instead of steps to recalculate run expirations
 * Stop writing to FlowRun.message_ids

v3.0.395
----------
 * Change FlowRun.get_last_msg to use message events instead of FlowRun.message_ids
 * Stop saving message associations with steps

v3.0.393
----------
 * Drop values_value

v3.0.392
----------
 * Remove broadcast purging

v3.0.391
----------
 * remove reference to nyaruka for trackings users
 * fix test decoration to work when no flow server configured

v3.0.390
----------
 * Disable webhook calls during flowserver trials
 * Use FlowRun.events for recent messages rollovers

v3.0.389
----------
 * add archive model, migrations

v3.0.388
----------
 * Make ContactField header clickable when sorting
 * Add first python2 incompatible code change
 * Add contact groups sheet on contact exports
 * Remove contact export as CSV
 * Update to latest goflow
 * Fix test_db contact fields serialization

v3.0.387
----------
 * fix flowstarts migration

v3.0.386
----------
 * update start contact migration to work with malformed extra

v3.0.384
----------
 * fix not selecting contact id from ES in canary task

v3.0.383
----------
 * add canary task for elasticsearch
 * record metrics about flowserver trial to librarto
 * allow sorting of contact fields via dragging in manage dialog

v3.0.382
----------
 * rename flow migration

v3.0.381
----------
 * limit number of flows exited at once, order by expired_on to encourage index
 * remove python 2.7 build target in travis
 * start flow starts in the flows queue vs our global celery one
 * add flow start count model to track # of runs in a flow start
 * Always use channel.name for channel assets

v3.0.380
----------
 * update to latest goflow to get location support
 * better output logs for goflow differences

v3.0.379
----------
 * add v2 editor through /v2 command in simulator

v3.0.378
----------
 * get all possible existing Twilio numbers on the Twilio account
 * reenable group sends *
 * remove Value model usage, Contact.search

v3.0.377
----------
 * do not allow dupe broadcasts to groups
 * Use ElasticSearch to export contacts and create dynamic groups
 * remove celery super auto scaler
 * update whatsapp activation by setting rate limits using new endpoints
 * fix incorrect keys for tokens and account sids for twiml apps
 * add ability to test flow results against goflow

v3.0.376
----------
 * remove celery super auto scaler since we don't use it anywhere
 * update whatsapp activation by setting rate limits using new endpoints
 * fix incorrect keys for tokens and account sids for twiml apps
 * add admin command to help audit ES and DB discrepencies

v3.0.375
----------
 * update whatsapp for new API
 * new index on contacts_contact.fields optimized for space

v3.0.374
----------
 * allow reading, just not writing of sends with groups
 * remove old seaching from contact views

v3.0.373
----------
 * optimize group views
 * don't allow sends to groups to be imported or copied
 * remove normal junebug, keep only junebug ussd
 * fix isset/~isset, sort by 'modified_on_mu' in ES
 * use ES to search for contacts

v3.0.372
----------
 * remap sms and status Twilio urls, log people still calling old ones
 * fix to display Export buttons on sent msgs folder and failed msgs folder
 * use message events in run.events for results exports instead of run.message_ids

v3.0.371
----------
 * add twilio messaging handling back in

v3.0.370
----------
 * remove logging of base handler being called

v3.0.369
----------
 * rename contact field types of decimal to number
 * finalize contact imports so that updated contacts have modified_on outside transaction 
 * try to fetch IVR recordings for up to a minute before giving up
 * remove handling and sendind code for all channel types (except twitter and junebug)

v3.0.368
----------
 * Fewer sentry errors from ES searching
 * Don't assume messages have a UUID in FlowRun.add_messages

v3.0.367
----------
 * allow up to two minutes for elastic search lag

v3.0.366
----------
 * fix empty queryset case for ES comparison

v3.0.365
----------
 * chill the f out with sentry if the first contact in our queryset is less than 30 seconds old
 * fix duplicate messages when searching on msgs whose contacts have more than one urn

v3.0.364
----------
 * fix environment variable for elastic search, catch all exceptions

v3.0.363
----------
 * Add Elastic searching for contacts, for now only validating that results through ES are the same as through postgres searches

v3.0.361
----------
 * Migrate Dart/Hub9 Contact urns and channels to support ext schemes

v3.0.360
----------
 * Use more efficient queries for check channels task
 * Fix Location geojson import

v3.0.359
----------
 * Add API endpoint to view failed messages

v3.0.358
----------
 * Allow filtering by uuid on runs API endpoint, and include run uuid in webhooks
 * Fix blockstrans failing on label count

v3.0.357
----------
 * Add linear backdown for our refresh rate on inbox pages

v3.0.356
----------
 * Do not log MageHandler calls
 * Serialize contact field label as name instead

v3.0.355
----------
 * Use force_text on uuids read from redis
 * Log errors for any channel handler methods

v3.0.354
----------
 * Set placeholder msg.id = 0
 * Fix comparison when price is None

v3.0.353
----------
 * Evaluate contact field with no value as False

v3.0.352
----------
 * Update to Facebook graph api v2.12

v3.0.351
----------
 * Support plain ISO dates (not just datetimes)

v3.0.350
----------
 * Swallow exceptions encountered when parsing, don't add to group
 * Set placeholder msg.id = 0

v3.0.349
----------
 * Deal with null state values in contact search evaluation

v3.0.348
----------
 * Fix off by one error in calculating best channel based on prefixes
 * Reevaluate dynamic groups using local contact fields instead of SQL

v3.0.347
----------
 * Add modified_on index for elasticsearch

v3.0.346
----------
 * Don't start archived flows
 * Don't show stale dates on campaign events
 * Allow brands to configure flow types
 * Remove group search from send to others action
 * Fixes for test contact activity

v3.0.345
----------
 * Migration to backfill run.events and add step uuids to run.path
 * Do the right thing when we are presented with NaN decimals

v3.0.344
----------
 * Use real JSONField for FlowRun.events
 * Add FlowRun.events and start populating with msg events for new runs
 * Serialize Contact.fields in test_db
 * Update to latest goflow release

v3.0.342
----------
 * Fix for decimal values in JSON fields attribute
 * Fix for not being able to change contact field types if campaign event inactive

v3.0.341
----------
 * Add if not exists to index creation for fields
 * Last of Py3 compatibility changes

v3.0.340
----------
 * Use fields JSON field on Contact instead of Value table for all reading.
 * Force campaign events to be based off of DateTime fields
 * Migration to change all contact fields used in campaign events to DateTime
 * Migration to add GIN index on Contact.fields

v3.0.339
----------
 * Remove leading and trailing spaces on location string before boundaries path query
 * Require use of update_fields with Contact.save()
 * Event time of contact_changed is when contact was modified
 * Use latest goflow release
 * Make special channel accessible during simulator use

v3.0.338
----------
 * Always serialize contact field datetime values in the org timezone
 * Add migration for population of the contact field json

v3.0.336
----------
 * Update middlewares to Django defaults for security
 * Add JSON fields to Contact, set in set_field
 * backfill any null location paths, make not null, update import to set path, set other levels on fields when setting location

v3.0.335
----------
 * Allow groups when scheduling flows or triggers
 * Fix configuration page URLs and use courier URLs
 * Replace contact.channel in goflow serialization with a channel query param in each contact URN
 * Serialize contact.group_uuids as groups with name and UUID

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
