from __future__ import unicode_literals

import json
import logging
import numbers
import phonenumbers
import pytz
import regex
import time
import urllib2
import xlwt
import re

from collections import OrderedDict, defaultdict
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.files.temp import NamedTemporaryFile
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User, Group
from django.db import models, connection
from django.db.models import Q, Count, QuerySet, Sum
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _, ungettext_lazy as _n
from django.utils.html import escape
from enum import Enum
from redis_cache import get_redis_connection
from smartmin.models import SmartModel
from temba.airtime.models import AirtimeTransfer
from temba.contacts.models import Contact, ContactGroup, ContactField, ContactURN, URN, TEL_SCHEME, NEW_CONTACT_VARIABLE
from temba.channels.models import Channel
from temba.locations.models import AdminBoundary, STATE_LEVEL, DISTRICT_LEVEL, WARD_LEVEL
from temba.msgs.models import Broadcast, Msg, FLOW, INBOX, INCOMING, QUEUED, INITIALIZING, HANDLED, SENT, Label, PENDING
from temba.msgs.models import INTERRUPTED, OUTGOING, UnreachableException
from temba.orgs.models import Org, Language, UNREAD_FLOW_MSGS, CURRENT_EXPORT_VERSION
from temba.utils import get_datetime_format, str_to_datetime, datetime_to_str, analytics, json_date_to_datetime, chunk_list
from temba.utils.email import send_template_email, is_valid_address
from temba.utils.models import TembaModel, ChunkIterator, generate_uuid
from temba.utils.profiler import SegmentProfiler
from temba.utils.queues import push_task
from temba.values.models import Value
from twilio import twiml
from uuid import uuid4

logger = logging.getLogger(__name__)

FLOW_DEFAULT_EXPIRES_AFTER = 60 * 12
START_FLOW_BATCH_SIZE = 500


class FlowException(Exception):
    def __init__(self, *args, **kwargs):
        super(FlowException, self).__init__(*args, **kwargs)


FLOW_LOCK_TTL = 60  # 1 minute
FLOW_LOCK_KEY = 'org:%d:lock:flow:%d:%s'

FLOW_PROP_CACHE_KEY = 'org:%d:cache:flow:%d:%s'
FLOW_PROP_CACHE_TTL = 24 * 60 * 60 * 7  # 1 week
FLOW_STAT_CACHE_KEY = 'org:%d:cache:flow:%d:%s'

UNREAD_FLOW_RESPONSES = 'unread_flow_responses'

# the most frequently we will check if our cache needs rebuilding
FLOW_STAT_CACHE_FREQUENCY = 24 * 60 * 60  # 1 day


class FlowLock(Enum):
    """
    Locks that are flow specific
    """
    participation = 1
    activity = 2
    definition = 3


class FlowPropsCache(Enum):
    """
    Properties of a flow that we cache
    """
    terminal_nodes = 1
    category_nodes = 2


class FlowStatsCache(Enum):
    """
    Stats we calculate and cache for flows
    """
    runs_started_count = 1    # deprecated, no longer used
    runs_completed_count = 2  # deprecated, no longer used
    contacts_started_set = 3  # deprecated, no longer used
    visit_count_map = 4
    step_active_set = 5
    cache_check = 6


def edit_distance(s1, s2):  # pragma: no cover
    """
    Compute the Damerau-Levenshtein distance between two given
    strings (s1 and s2)
    """
    # if first letters are different, infinite distance
    if s1 and s2 and s1[0] != s2[0]:
        return 100

    d = {}
    lenstr1 = len(s1)
    lenstr2 = len(s2)

    for i in xrange(-1, lenstr1 + 1):
        d[(i, -1)] = i + 1
    for j in xrange(-1, lenstr2 + 1):
        d[(-1, j)] = j + 1

    for i in xrange(0, lenstr1):
        for j in xrange(0, lenstr2):
            if s1[i] == s2[j]:
                cost = 0
            else:
                cost = 1
            d[(i, j)] = min(
                d[(i - 1, j)] + 1,  # deletion
                d[(i, j - 1)] + 1,  # insertion
                d[(i - 1, j - 1)] + cost,  # substitution
            )
            if i > 1 and j > 1 and s1[i] == s2[j - 1] and s1[i - 1] == s2[j]:
                d[(i, j)] = min(d[(i, j)], d[i - 2, j - 2] + cost)  # transposition

    return d[lenstr1 - 1, lenstr2 - 1]


class Flow(TembaModel):
    UUID = 'uuid'
    ENTRY = 'entry'
    RULE_SETS = 'rule_sets'
    ACTION_SETS = 'action_sets'
    RULES = 'rules'
    CONFIG = 'config'
    ACTIONS = 'actions'
    DESTINATION = 'destination'
    LABEL = 'label'
    WEBHOOK_URL = 'webhook'
    WEBHOOK_ACTION = 'webhook_action'
    FINISHED_KEY = 'finished_key'
    RULESET_TYPE = 'ruleset_type'
    OPERAND = 'operand'
    METADATA = 'metadata'

    BASE_LANGUAGE = 'base_language'
    SAVED_BY = 'saved_by'
    VERSION = 'version'

    CONTACT_CREATION = 'contact_creation'
    CONTACT_PER_RUN = 'run'
    CONTACT_PER_LOGIN = 'login'

    SAVED_ON = 'saved_on'
    NAME = 'name'
    REVISION = 'revision'
    FLOW_TYPE = 'flow_type'
    ID = 'id'
    EXPIRES = 'expires'

    X = 'x'
    Y = 'y'

    FLOW = 'F'
    MESSAGE = 'M'
    VOICE = 'V'
    SURVEY = 'S'
    USSD = 'U'

    RULES_ENTRY = 'R'
    ACTIONS_ENTRY = 'A'

    FLOW_TYPES = ((FLOW, _("Message flow")),
                  (MESSAGE, _("Single Message Flow")),
                  (VOICE, _("Phone call flow")),
                  (SURVEY, _("Android Survey")),
                  (USSD, _("USSD flow")))

    ENTRY_TYPES = ((RULES_ENTRY, "Rules"),
                   (ACTIONS_ENTRY, "Actions"))

    name = models.CharField(max_length=64,
                            help_text=_("The name for this flow"))

    labels = models.ManyToManyField('FlowLabel', related_name='flows', verbose_name=_("Labels"), blank=True,
                                    help_text=_("Any labels on this flow"))

    org = models.ForeignKey(Org, related_name='flows')

    entry_uuid = models.CharField(null=True, max_length=36, unique=True)

    entry_type = models.CharField(max_length=1, null=True, choices=ENTRY_TYPES,
                                  help_text=_("The type of node this flow starts with"))

    is_archived = models.BooleanField(default=False,
                                      help_text=_("Whether this flow is archived"))

    flow_type = models.CharField(max_length=1, choices=FLOW_TYPES, default=FLOW,
                                 help_text=_("The type of this flow"))

    metadata = models.TextField(null=True, blank=True,
                                help_text=_("Any extra metadata attached to this flow, strictly used by the user interface."))

    expires_after_minutes = models.IntegerField(default=FLOW_DEFAULT_EXPIRES_AFTER,
                                                help_text=_("Minutes of inactivity that will cause expiration from flow"))

    ignore_triggers = models.BooleanField(default=False,
                                          help_text=_("Ignore keyword triggers while in this flow"))

    saved_on = models.DateTimeField(auto_now_add=True,
                                    help_text=_("When this item was saved"))

    saved_by = models.ForeignKey(User, related_name="flow_saves",
                                 help_text=_("The user which last saved this flow"))

    base_language = models.CharField(max_length=4, null=True, blank=True,
                                     help_text=_('The primary language for editing this flow'),
                                     default='base')

    version_number = models.IntegerField(default=CURRENT_EXPORT_VERSION,
                                         help_text=_("The flow version this definition is in"))

    @classmethod
    def create(cls, org, user, name, flow_type=FLOW, expires_after_minutes=FLOW_DEFAULT_EXPIRES_AFTER, base_language=None):
        flow = Flow.objects.create(org=org, name=name, flow_type=flow_type,
                                   expires_after_minutes=expires_after_minutes, base_language=base_language,
                                   saved_by=user, created_by=user, modified_by=user)

        analytics.track(user.username, 'nyaruka.flow_created', dict(name=name))
        return flow

    @classmethod
    def create_single_message(cls, org, user, message):
        """
        Creates a special 'single message' flow
        """
        name = 'Single Message (%s)' % unicode(uuid4())
        flow = Flow.create(org, user, name, flow_type=Flow.MESSAGE)
        flow.update_single_message_flow(message)
        return flow

    @classmethod
    def label_to_slug(cls, label):
        return regex.sub(r'[^a-z0-9]+', '_', label.lower(), regex.V0)

    @classmethod
    def create_join_group(cls, org, user, group, response=None, start_flow=None):
        """
        Creates a special 'join group' flow
        """
        base_language = org.primary_language.iso_code if org.primary_language else 'base'

        name = Flow.get_unique_name(org, 'Join %s' % group.name)
        flow = Flow.create(org, user, name, base_language=base_language)

        uuid = unicode(uuid4())
        actions = [dict(type='add_group', group=dict(uuid=group.uuid, name=group.name)),
                   dict(type='save', field='name', label='Contact Name', value='@(PROPER(REMOVE_FIRST_WORD(step.value)))')]

        if response:
            actions += [dict(type='reply', msg={base_language: response})]

        if start_flow:
            actions += [dict(type='flow', flow=dict(uuid=start_flow.uuid, name=start_flow.name))]

        action_sets = [dict(x=100, y=0, uuid=uuid, actions=actions)]
        flow.update(dict(entry=uuid, base_language=base_language,
                         rule_sets=[], action_sets=action_sets))

        return flow

    @classmethod
    def import_flows(cls, exported_json, org, user, same_site=False):
        """
        Import flows from our flow export file
        """
        created_flows = []
        flow_uuid_map = dict()

        # create all the flow containers first
        for flow_spec in exported_json['flows']:

            FlowRevision.validate_flow_definition(flow_spec)

            flow_type = flow_spec.get('flow_type', Flow.FLOW)
            name = flow_spec['metadata']['name'][:64].strip()

            flow = None

            # Don't create our campaign message flows, we'll do that later
            # this check is only needed up to version 3 of exports
            if flow_type != Flow.MESSAGE:
                # check if we can find that flow by id first
                if same_site:
                    flow = Flow.objects.filter(org=org, is_active=True, uuid=flow_spec['metadata']['uuid']).first()
                    if flow:
                        flow.expires_after_minutes = flow_spec['metadata'].get('expires', FLOW_DEFAULT_EXPIRES_AFTER)
                        flow.name = Flow.get_unique_name(org, name, ignore=flow)
                        flow.save(update_fields=['name', 'expires_after_minutes'])

                # if it's not of our world, let's try by name
                if not flow:
                    flow = Flow.objects.filter(org=org, is_active=True, name=name).first()

                # if there isn't one already, create a new flow
                if not flow:
                    flow = Flow.create(org, user, Flow.get_unique_name(org, name), flow_type=flow_type,
                                       expires_after_minutes=flow_spec['metadata'].get('expires', FLOW_DEFAULT_EXPIRES_AFTER))

                created_flows.append(dict(flow=flow, flow_spec=flow_spec))

                if 'uuid' in flow_spec['metadata']:
                    flow_uuid_map[flow_spec['metadata']['uuid']] = flow.uuid

        # now let's update our flow definitions with any referenced flows
        def remap_flow(element):
            # first map our id accordingly
            if element['uuid'] in flow_uuid_map:
                element['uuid'] = flow_uuid_map[element['uuid']]

            existing_flow = Flow.objects.filter(uuid=element['uuid'], org=org, is_active=True).first()
            if not existing_flow:
                existing_flow = Flow.objects.filter(org=org, name=element['name'], is_active=True).first()
                if existing_flow:
                    element['uuid'] = existing_flow.uuid

        for created in created_flows:
            for ruleset in created['flow_spec'][Flow.RULE_SETS]:
                if ruleset['ruleset_type'] == RuleSet.TYPE_SUBFLOW:
                    remap_flow(ruleset['config']['flow'])

            for actionset in created['flow_spec'][Flow.ACTION_SETS]:
                for action in actionset['actions']:
                    if action['type'] in ['flow', 'trigger-flow']:
                        remap_flow(action['flow'])
            remap_flow(created['flow_spec']['metadata'])
            created['flow'].import_definition(created['flow_spec'])

        # remap our flow ids according to how they were resolved
        if 'campaigns' in exported_json:
            for campaign in exported_json['campaigns']:
                for event in campaign['events']:
                    if 'flow' in event:
                        flow_uuid = event['flow']['uuid']
                        if flow_uuid in flow_uuid_map:
                            event['flow']['uuid'] = flow_uuid_map[flow_uuid]

        if 'triggers' in exported_json:
            for trigger in exported_json['triggers']:
                if 'flow' in trigger:
                    flow_uuid = trigger['flow']['uuid']
                    if flow_uuid in flow_uuid_map:
                        trigger['flow']['uuid'] = flow_uuid_map[flow_uuid]

        return exported_json

    @classmethod
    def copy(cls, flow, user):
        copy = Flow.create(flow.org, user, "Copy of %s" % flow.name[:55], flow_type=flow.flow_type)

        # grab the json of our original
        flow_json = flow.as_json()

        copy.import_definition(flow_json)

        # copy our expiration as well
        copy.expires_after_minutes = flow.expires_after_minutes
        copy.save()

        return copy

    @classmethod
    def get_node(cls, flow, uuid, destination_type):

        if not uuid or not destination_type:
            return None

        if destination_type == FlowStep.TYPE_RULE_SET:
            return RuleSet.get(flow, uuid)
        else:
            return ActionSet.get(flow, uuid)

    @classmethod
    def handle_call(cls, call, user_response=None, hangup=False):
        if not user_response:
            user_response = {}

        flow = call.flow
        run = FlowRun.objects.filter(call=call).first()

        # make sure we have the latest version
        flow.ensure_current_version()

        # what we will send back
        voice_response = twiml.Response()
        run.voice_response = voice_response

        # make sure our test contact is handled by simulation
        if call.contact.is_test:
            Contact.set_simulation(True)

        # parse the user response
        text = user_response.get('Digits', None)
        media_url = user_response.get('RecordingUrl', None)

        # if we've been sent a recording, go grab it
        if media_url:
            media_url = call.channel.get_ivr_client().download_media(media_url)

        # create a message to hold our inbound message
        from temba.msgs.models import IVR
        if text is not None or media_url:

            # we don't have text for media, so lets use the media value there too
            if media_url and ':' in media_url:
                text = media_url.partition(':')[2]

            msg = Msg.create_incoming(call.channel, call.contact_urn.urn,
                                      text, status=PENDING, msg_type=IVR, media=media_url)
        else:
            msg = Msg(org=call.org, contact=call.contact, text='', id=0)

        # find out where we last left off
        step = run.steps.all().order_by('-arrived_on').first()

        # if we are just starting the flow, create our first step
        if not step:
            # lookup our entry node
            destination = ActionSet.objects.filter(flow=run.flow, uuid=flow.entry_uuid).first()
            if not destination:
                destination = RuleSet.objects.filter(flow=run.flow, uuid=flow.entry_uuid).first()

            # and add our first step for our run
            if destination:
                step = flow.add_step(run, destination, [], call=call)

        # go and actually handle wherever we are in the flow
        destination = Flow.get_node(run.flow, step.step_uuid, step.step_type)
        (handled, msgs) = Flow.handle_destination(destination, step, run, msg, user_input=text is not None)

        # if we stopped needing user input (likely), then wrap our response accordingly
        voice_response = Flow.wrap_voice_response_with_input(call, run, voice_response)

        # if we handled it, increment our unread count
        if handled and not call.contact.is_test:
            run.flow.increment_unread_responses()
            if msg.id:
                Msg.mark_handled(msg)

        # if we didn't handle it, this is a good time to hangup
        if not handled or hangup:
            voice_response.hangup()
            run.set_completed(final_step=step)

        return voice_response

    @classmethod
    def wrap_voice_response_with_input(cls, call, run, voice_response):
        """ Finds where we are in the flow and wraps our voice_response with whatever comes next """
        step = run.steps.all().order_by('-pk').first()
        destination = Flow.get_node(run.flow, step.step_uuid, step.step_type)
        if isinstance(destination, RuleSet):
            response = twiml.Response()
            callback = 'https://%s%s' % (settings.TEMBA_HOST, reverse('ivr.ivrcall_handle', args=[call.pk]))
            gather = destination.get_voice_input(response, action=callback)

            # recordings have to be tacked on last
            if destination.ruleset_type == RuleSet.TYPE_WAIT_RECORDING:
                voice_response.record(action=callback)
            elif gather:
                # nest all of our previous verbs in our gather
                for verb in voice_response.verbs:
                    gather.append(verb)

                voice_response = response

                # append a redirect at the end in case the user sends #
                voice_response.append(twiml.Redirect(url=callback + "?empty=1"))

        return voice_response

    @classmethod
    def get_unique_name(cls, org, base_name, ignore=None):
        """
        Generates a unique flow name based on the given base name
        """
        name = base_name[:64].strip()

        count = 2
        while True:
            flows = Flow.objects.filter(name=name, org=org, is_active=True)
            if ignore:
                flows = flows.exclude(pk=ignore.pk)

            if not flows.exists():
                break

            name = '%s %d' % (base_name[:59].strip(), count)
            count += 1

        return name

    @classmethod
    def find_and_handle(cls, msg, started_flows=None, voice_response=None,
                        triggered_start=False, resume_parent_run=False, resume_after_timeout=False):

        if started_flows is None:
            started_flows = []

        steps = FlowStep.get_active_steps_for_contact(msg.contact, step_type=FlowStep.TYPE_RULE_SET)
        for step in steps:
            flow = step.run.flow
            flow.ensure_current_version()
            destination = Flow.get_node(flow, step.step_uuid, step.step_type)

            # this node doesn't exist anymore, mark it as left so they leave the flow
            if not destination:  # pragma: no cover
                step.run.set_completed(final_step=step)
                continue

            (handled, msgs) = Flow.handle_destination(destination, step, step.run, msg, started_flows,
                                                      user_input=True, triggered_start=triggered_start,
                                                      resume_parent_run=resume_parent_run,
                                                      resume_after_timeout=resume_after_timeout)

            if handled:
                # increment our unread count if this isn't the simulator
                if not msg.contact.is_test:
                    flow.increment_unread_responses()

                return True

        return False

    @classmethod
    def handle_destination(cls, destination, step, run, msg,
                           started_flows=None, is_test_contact=False, user_input=False,
                           triggered_start=False, trigger_send=True, resume_parent_run=False, resume_after_timeout=False):

        if started_flows is None:
            started_flows = []

        def add_to_path(path, uuid):
            if uuid in path:
                path.append(uuid)
                raise FlowException("Flow cycle detected at runtime: %s" % path)
            path.append(uuid)

        start_time = time.time()
        path = []
        msgs = []

        # lookup our next destination
        handled = False
        while destination:
            result = {"handled": False}

            if destination.get_step_type() == FlowStep.TYPE_RULE_SET:
                should_pause = False

                # check if we need to stop
                if destination.is_pause() or msg.status == HANDLED:
                    should_pause = True

                if triggered_start and destination.is_ussd():
                    result = Flow.handle_ussd_ruleset_action(destination, step, run, msg)
                    msgs += result['msgs']
                if (user_input or resume_after_timeout) or not should_pause:
                    result = Flow.handle_ruleset(destination, step, run, msg, started_flows, resume_parent_run,
                                                 resume_after_timeout)
                    add_to_path(path, destination.uuid)
                # USSD ruleset has extra functionality to send out messages.
                # This is handled as a shadow step for the ruleset.
                elif destination.is_ussd():
                    result = Flow.handle_ussd_ruleset_action(destination, step, run, msg)
                    msgs += result['msgs']

                # if we used this input, then mark our user input as used
                if should_pause:
                    user_input = False

                    # once we handle user input, reset our path
                    path = []

            elif destination.get_step_type() == FlowStep.TYPE_ACTION_SET:
                result = Flow.handle_actionset(destination, step, run, msg, started_flows, is_test_contact)
                add_to_path(path, destination.uuid)

                # add any generated messages to be sent at once
                msgs += result['msgs']

            # if this is a triggered start, we only consider user input on the first step, so clear it now
            if triggered_start:
                user_input = False

            # pull out our current state from the result
            step = result.get('step')

            # lookup our next destination
            destination = result.get('destination', None)

            # if any one of our destinations handled us, consider it handled
            if result.get('handled', False):
                handled = True

            resume_parent_run = False
            resume_after_timeout = False

        if handled:
            analytics.gauge('temba.flow_execution', time.time() - start_time)

        # send any messages generated
        if msgs and trigger_send:
            msgs.sort(key=lambda message: message.created_on)
            run.flow.org.trigger_send(msgs)

        return handled, msgs

    @classmethod
    def handle_actionset(cls, actionset, step, run, msg, started_flows, is_test_contact=False):

        # not found, escape out, but we still handled this message, user is now out of the flow
        if not actionset:  # pragma: no cover
            run.set_completed(final_step=step)
            return dict(handled=True, destination=None, destination_type=None)

        # actually execute all the actions in our actionset
        msgs = actionset.execute_actions(run, msg, started_flows)

        for msg in msgs:
            step.add_message(msg)

        # and onto the destination
        destination = Flow.get_node(actionset.flow, actionset.destination, actionset.destination_type)
        if destination:
            arrived_on = timezone.now()
            step.left_on = arrived_on
            step.next_uuid = destination.uuid
            step.save(update_fields=['left_on', 'next_uuid'])

            step = run.flow.add_step(run, destination, previous_step=step, arrived_on=arrived_on)
        else:
            run.set_completed(final_step=step)
            step = None

        return dict(handled=True, destination=destination, step=step, msgs=msgs)

    @classmethod
    def handle_ruleset(cls, ruleset, step, run, msg, started_flows, resume_parent_run=False, resume_after_timeout=False):

        if msg.status == INTERRUPTED:  # check interrupt
            rule, value = ruleset.find_interrupt_rule(step, run, msg)
            if not rule:
                run.set_interrupted(final_step=step)
                return dict(handled=True, destination=None, destination_type=None)
        else:
            if ruleset.ruleset_type == RuleSet.TYPE_SUBFLOW:
                if not resume_parent_run:
                    flow_uuid = json.loads(ruleset.config).get('flow').get('uuid')
                    flow = Flow.objects.filter(org=run.org, uuid=flow_uuid).first()
                    message_context = run.flow.build_message_context(run.contact, msg)

                    # our extra will be the current flow variables
                    extra = message_context.get('extra', {})
                    extra['flow'] = message_context.get('flow', {})

                    if msg.id > 0:
                        step.add_message(msg)
                        run.update_expiration(timezone.now())

                    if flow:
                        flow.start([], [run.contact], started_flows=started_flows, restart_participants=True,
                                   extra=extra, parent_run=run, interrupt=False)
                        return dict(handled=True, destination=None, destination_type=None)
            # find a matching rule
            rule, value = ruleset.find_matching_rule(step, run, msg, resume_after_timeout=resume_after_timeout)

        flow = ruleset.flow

        # add the message to our step
        if msg.id > 0:
            step.add_message(msg)
            run.update_expiration(timezone.now())

        if ruleset.ruleset_type in RuleSet.TYPE_MEDIA:
            # store the media path as the value
            value = msg.media.split(':', 1)[1]

        if not msg.status == INTERRUPTED:
            step.save_rule_match(rule, value)
            ruleset.save_run_value(run, rule, value)

        # output the new value if in the simulator
        if run.contact.is_test:
            if msg.status == INTERRUPTED:
                ActionLog.create(run, _("@flow.%s has been interrupted") % (Flow.label_to_slug(ruleset.label)))
            else:
                ActionLog.create(run, _("Saved '%s' as @flow.%s") % (value, Flow.label_to_slug(ruleset.label)))

        # no destination for our rule?  we are done, though we did handle this message, user is now out of the flow
        if not rule.destination:
            if msg.status == INTERRUPTED:
                # run was interrupted and interrupt state not handled (not connected)
                run.set_interrupted(final_step=step)
            else:
                # log it for our test contacts
                run.set_completed(final_step=step)

            return dict(handled=True, destination=None, destination_type=None)

        # Create the step for our destination
        destination = Flow.get_node(flow, rule.destination, rule.destination_type)
        if destination:
            arrived_on = timezone.now()
            step.left_on = arrived_on
            step.next_uuid = rule.destination
            step.save(update_fields=['left_on', 'next_uuid'])
            step = flow.add_step(run, destination, rule=rule.uuid, category=rule.category, previous_step=step)
        return dict(handled=True, destination=destination, step=step)

    @classmethod
    def handle_ussd_ruleset_action(cls, ruleset, step, run, msg):
        action = UssdAction.from_ruleset(ruleset, run)
        msgs = action.execute(run, ruleset.uuid, msg)

        for msg in msgs:
            step.add_message(msg)

        return dict(handled=True, destination=None, step=step, msgs=msgs)

    @classmethod
    def apply_action_label(cls, user, flows, label, add):
        return label.toggle_label(flows, add)

    @classmethod
    def apply_action_archive(cls, user, flows):
        changed = []

        for flow in flows:
            flow.archive()
            changed.append(flow.pk)

        return changed

    @classmethod
    def apply_action_restore(cls, user, flows):
        changed = []
        for flow in flows:
            try:
                flow.restore()
                changed.append(flow.pk)
            except FlowException:  # pragma: no cover
                pass
        return changed

    @classmethod
    def build_flow_context(cls, flow, contact, contact_context=None):
        """
        Get a flow context built on the last run for the contact in the given flow
        """

        date_format = get_datetime_format(flow.org.get_dayfirst())[1]
        tz = pytz.timezone(flow.org.timezone)

        # wrapper around our value dict, lets us do a nice representation of both @flow.foo and @flow.foo.text
        def value_wrapper(value):
            values = dict(text=value['text'],
                          time=datetime_to_str(value['time'], format=date_format, tz=tz),
                          category=flow.get_localized_text(value['category'], contact),
                          value=unicode(value['rule_value']))
            values['__default__'] = unicode(value['rule_value'])
            return values

        flow_context = {}
        values = []
        if contact:
            results = flow.get_results(contact, only_last_run=True)
            if results and results[0]:
                for value in results[0]['values']:
                    field = Flow.label_to_slug(value['label'])
                    flow_context[field] = value_wrapper(value)
                    values.append("%s: %s" % (value['label'], value['rule_value']))

            flow_context['__default__'] = "\n".join(values)

            # if we don't have a contact context, build one
            if not contact_context:
                flow_context['contact'] = contact.build_message_context()

        return flow_context

    def as_select2(self):
        return dict(id=self.uuid, text=self.name)

    def release(self):
        """
        Releases this flow, marking it inactive. We remove all flow runs, steps and values in a background process.
        We keep FlowRevisions and FlowStarts however.
        """
        from .tasks import delete_flow_results_task

        self.is_active = False
        self.save()

        # release any campaign events that depend on this flow
        from temba.campaigns.models import CampaignEvent
        for event in CampaignEvent.objects.filter(flow=self, is_active=True):
            event.release()

        # release any triggers that depend on this flow
        from temba.triggers.models import Trigger
        for trigger in Trigger.objects.filter(flow=self, is_active=True):
            trigger.release()

        # delete our results in the background
        delete_flow_results_task.delay(self.id)

    def delete_results(self):
        """
        Removes all flow runs, values and steps for a flow.
        """
        # grab the ids of all our runs
        run_ids = self.runs.all().values_list('id', flat=True)

        # in chunks of 1000, remove any values or flowsteps associated with these runs
        # we keep Runs around for auditing purposes
        for chunk in chunk_list(run_ids, 1000):
            Value.objects.filter(run__in=chunk).delete()
            FlowStep.objects.filter(run__in=chunk).delete()

        # clear all our cached stats
        self.clear_props_cache()
        self.clear_stats_cache()

    def clear_props_cache(self):
        r = get_redis_connection()
        keys = [self.get_props_cache_key(c) for c in FlowPropsCache.__members__.values()]
        r.delete(*keys)

    def clear_stats_cache(self):
        r = get_redis_connection()
        keys = [self.get_stats_cache_key(c) for c in FlowStatsCache.__members__.values()]
        r.delete(*keys)

    def get_props_cache_key(self, kind):
        return FLOW_PROP_CACHE_KEY % (self.org_id, self.pk, kind.name)

    def get_stats_cache_key(self, kind, item=None):
        name = kind
        if hasattr(kind, 'name'):
            name = kind.name

        cache_key = FLOW_STAT_CACHE_KEY % (self.org_id, self.pk, name)
        if item:
            cache_key += (':%s' % item)
        return cache_key

    def calculate_active_step_keys(self):
        """
        Returns a list of UUIDs for all ActionSets and RuleSets on this flow.
        :return:
        """
        # first look up any action set uuids
        steps = list(self.action_sets.values('uuid'))

        # then our ruleset uuids
        steps += list(self.rule_sets.values('uuid'))

        # extract just the uuids
        return [self.get_stats_cache_key(FlowStatsCache.step_active_set, step['uuid']) for step in steps]

    def lock_on(self, lock, qualifier=None, lock_ttl=None):
        """
        Creates the requested type of flow-level lock
        """
        r = get_redis_connection()
        lock_key = FLOW_LOCK_KEY % (self.org_id, self.pk, lock.name)
        if qualifier:
            lock_key += (":%s" % qualifier)

        if not lock_ttl:
            lock_ttl = FLOW_LOCK_TTL

        return r.lock(lock_key, lock_ttl)

    def do_calculate_flow_stats(self, lock_ttl=None):
        r = get_redis_connection()

        # activity
        with self.lock_on(FlowLock.activity, lock_ttl=lock_ttl):
            (active, visits) = self._calculate_activity()

            # remove our old active cache
            keys = self.calculate_active_step_keys()
            if keys:
                r.delete(*keys)
            r.delete(self.get_stats_cache_key(FlowStatsCache.visit_count_map))

            # add current active cache
            for step, runs in active.items():
                for run in runs:
                    r.sadd(self.get_stats_cache_key(FlowStatsCache.step_active_set, step), run)

            if len(visits):
                r.hmset(self.get_stats_cache_key(FlowStatsCache.visit_count_map), visits)

    def _calculate_activity(self, simulation=False):

        """
        Calculate our activity stats from the database. This is expensive. It should only be run
        for simulation or in an async task to rebuild the activity cache
        """
        # who is actively at each step
        steps = FlowStep.objects.values('run__pk', 'step_uuid').filter(run__is_active=True, run__flow=self, left_on=None, run__contact__is_test=simulation).annotate(count=Count('run_id'))

        active = {}
        for step in steps:
            step_id = step['step_uuid']
            if step_id not in active:
                active[step_id] = {step['run__pk']}
            else:
                active[step_id].add(step['run__pk'])

        # need to be a list for json
        for key, value in active.items():
            active[key] = list(value)

        visits = {}
        visited_actions = FlowStep.objects.values('step_uuid', 'next_uuid').filter(run__flow=self, step_type='A', run__contact__is_test=simulation).annotate(count=Count('run_id'))
        visited_rules = FlowStep.objects.values('rule_uuid', 'next_uuid').filter(run__flow=self, step_type='R', run__contact__is_test=simulation).exclude(rule_uuid=None).annotate(count=Count('run_id'))

        # where have people visited
        for step in visited_actions:
            if step['next_uuid'] and step['count']:
                visits['%s:%s' % (step['step_uuid'], step['next_uuid'])] = step['count']

        for step in visited_rules:
            if step['next_uuid'] and step['count']:
                visits['%s:%s' % (step['rule_uuid'], step['next_uuid'])] = step['count']

        return (active, visits)

    def _check_for_cache_update(self):
        """
        Checks if we have a redis cache for our flow stats, or whether they need to be updated.
        If so, triggers an async rebuild of the cache for our flow.
        """
        from .tasks import check_flow_stats_accuracy_task

        r = get_redis_connection()

        # don't do the more expensive check if it was performed recently
        cache_check = self.get_stats_cache_key(FlowStatsCache.cache_check)
        if r.exists(cache_check):
            return

        # don't check again for a day or so, add up to an hour of randomness
        # to spread things out a bit
        import random
        r.set(cache_check, 1, FLOW_STAT_CACHE_FREQUENCY + random.randint(0, 60 * 60))

        # check flow stats for accuracy, rebuilding if necessary
        check_flow_stats_accuracy_task.delay(self.pk)

    def get_activity(self, simulation=False, check_cache=True):
        """
        Get the activity summary for a flow as a tuple of the number of active runs
        at each step and a map of the previous visits
        """
        if simulation:
            (active, visits) = self._calculate_activity(simulation=True)
            # we want counts not actual run ids
            for key, value in active.items():
                active[key] = len(value)
            return (active, visits)

        if check_cache:
            self._check_for_cache_update()

        r = get_redis_connection()

        # get all possible active keys
        keys = self.calculate_active_step_keys()
        active = {}
        for key in keys:
            count = r.scard(key)
            # only include stats for steps that actually have people there
            if count:
                active[key[key.rfind(':') + 1:]] = count

        # visited path
        visited = r.hgetall(self.get_stats_cache_key(FlowStatsCache.visit_count_map))

        # make sure our counts are treated as ints for consistency
        for k, v in visited.items():
            visited[k] = int(v)

        return (active, visited)

    def get_total_runs(self):
        return FlowRunCount.run_count(self)

    def get_base_text(self, language_dict, default=''):
        if not isinstance(language_dict, dict):  # pragma: no cover
            return language_dict

        if self.base_language:
            return language_dict.get(self.base_language, default)

        return default  # pragma: no cover

    def get_localized_text(self, text_translations, contact=None, default_text=''):
        """
        Given a language dict and a preferred language, return the best possible text match
        :param text_translations: The text in all supported languages, or string (which will just return immediately)
        :param contact: the contact we are interacting with
        :param default_text: What to use if all else fails
        :return: the localized text
        """
        org_languages = {l.iso_code for l in self.org.languages.all()}

        # We return according to the following precedence:
        #   1) Contact's language (if it's a valid org language)
        #   2) Org Primary Language
        #   3) Flow Base Language
        #   4) Default Text
        preferred_languages = []

        if contact and contact.language and contact.language in org_languages:
            preferred_languages.append(contact.language)

        if self.org.primary_language:
            preferred_languages.append(self.org.primary_language.iso_code)

        preferred_languages.append(self.base_language)

        return Language.get_localized_text(text_translations, preferred_languages, default_text)

    def update_run_expirations(self):
        """
        Update all of our current run expirations according to our new expiration period
        """
        for step in FlowStep.objects.filter(run__flow=self, run__is_active=True, left_on=None).distinct('run'):
            step.run.update_expiration(step.arrived_on)

        # force an expiration update
        from temba.flows.tasks import check_flows_task
        check_flows_task.delay()

    def import_definition(self, flow_json):
        """
        Allows setting the definition for a flow from another definition.  All uuid's will be
        remmaped accordingly.
        """
        # uuid mappings
        uuid_map = dict()

        def copy_recording(url, path):
            if not url:
                return None

            try:
                url = "https://%s/%s" % (settings.AWS_BUCKET_DOMAIN, url)
                temp = NamedTemporaryFile(delete=True)
                temp.write(urllib2.urlopen(url).read())
                temp.flush()
                return default_storage.save(path, temp)
            except Exception:
                # its okay if its no longer there, we'll remove the recording
                return None

        def remap_uuid(json, attribute):
            if attribute in json and json[attribute]:
                uuid = json[attribute]
                new_uuid = uuid_map.get(uuid, None)
                if not new_uuid:
                    new_uuid = str(uuid4())
                    uuid_map[uuid] = new_uuid

                json[attribute] = new_uuid

        remap_uuid(flow_json, 'entry')
        for actionset in flow_json[Flow.ACTION_SETS]:
            remap_uuid(actionset, 'uuid')
            remap_uuid(actionset, 'destination')

            # for all of our recordings, pull them down and remap
            for action in actionset['actions']:
                if 'recording' in action:
                    # if its a localized
                    if isinstance(action['recording'], dict):
                        for lang, url in action['recording'].iteritems():
                            path = copy_recording(url, 'recordings/%d/%d/steps/%s.wav' % (self.org.pk, self.pk, action['uuid']))
                            action['recording'][lang] = path
                    else:
                        path = copy_recording(action['recording'], 'recordings/%d/%d/steps/%s.wav' % (self.org.pk, self.pk, action['uuid']))
                        action['recording'] = path

        for ruleset in flow_json[Flow.RULE_SETS]:
            remap_uuid(ruleset, 'uuid')
            for rule in ruleset.get('rules', []):
                remap_uuid(rule, 'uuid')
                remap_uuid(rule, 'destination')

        # now update with our remapped values
        self.update(flow_json)
        return self

    def set_metadata_json(self, metadata):
        self.metadata = json.dumps(metadata)

    def get_metadata_json(self):
        metadata = {}
        if self.metadata:
            metadata = json.loads(self.metadata)
        return metadata

    def archive(self):
        self.is_archived = True
        self.save(update_fields=['is_archived'])

        # archive our triggers as well
        from temba.triggers.models import Trigger
        Trigger.objects.filter(flow=self).update(is_archived=True)

    def restore(self):
        if self.flow_type == Flow.VOICE:
            if not self.org.supports_ivr():
                raise FlowException("%s requires a Twilio number")

        self.is_archived = False
        self.save(update_fields=['is_archived'])
        # we don't know enough to restore triggers automatically

    def update_single_message_flow(self, message):
        self.flow_type = Flow.MESSAGE
        self.save(update_fields=['name', 'flow_type'])

        uuid = str(uuid4())
        action_sets = [dict(x=100, y=0, uuid=uuid, actions=[dict(type='reply', msg=dict(base=message))])]
        self.update(dict(entry=uuid, rule_sets=[], action_sets=action_sets, base_language='base'))

    def steps(self):
        return FlowStep.objects.filter(run__flow=self)

    def get_completed_runs(self):
        return FlowRunCount.run_count_for_type(self, FlowRun.EXIT_TYPE_COMPLETED)

    def get_interrupted_runs(self):
        return FlowRunCount.run_count_for_type(self, FlowRun.EXIT_TYPE_INTERRUPTED)

    def get_expired_runs(self):
        return FlowRunCount.run_count_for_type(self, FlowRun.EXIT_TYPE_EXPIRED)

    def get_completed_percentage(self):
        total_runs = FlowRunCount.run_count(self)

        if not total_runs:
            return 0
        else:
            return int(self.get_completed_runs() * 100 / total_runs)

    def get_and_clear_unread_responses(self):
        """
        Gets the number of new responses since the last clearing for this flow.
        """
        r = get_redis_connection()

        # get the number of new responses
        new_responses = r.hget(UNREAD_FLOW_RESPONSES, self.id)

        # then clear them
        r.hdel(UNREAD_FLOW_RESPONSES, self.id)

        return 0 if new_responses is None else int(new_responses)

    def increment_unread_responses(self):
        """
        Increments the number of new responses for this flow.
        """
        r = get_redis_connection()
        r.hincrby(UNREAD_FLOW_RESPONSES, self.id, 1)

        # increment our global count as well
        self.org.increment_unread_msg_count(UNREAD_FLOW_MSGS)

    def get_columns(self):
        node_order = []
        for ruleset in RuleSet.objects.filter(flow=self).exclude(label=None).order_by('y', 'pk'):
            if ruleset.uuid:
                node_order.append(ruleset)

        return node_order

    def build_ruleset_caches(self, ruleset_list=None):
        rulesets = dict()
        rule_categories = dict()

        if ruleset_list is None:
            ruleset_list = RuleSet.objects.filter(flow=self).exclude(label=None).order_by('pk').select_related('flow', 'flow__org')

        for ruleset in ruleset_list:
            rulesets[ruleset.uuid] = ruleset
            for rule in ruleset.get_rules():
                rule_categories[rule.uuid] = rule.category

        return (rulesets, rule_categories)

    def build_message_context(self, contact, msg):
        contact_context = contact.build_message_context() if contact else dict()

        # our default value
        channel_context = None

        # add our message context
        if msg:
            message_context = msg.build_message_context()

            # some fake channel deets for simulation
            if msg.contact.is_test:
                channel_context = dict(__default__='(800) 555-1212', name='Simulator', tel='(800) 555-1212', tel_e164='+18005551212')
            elif msg.channel:
                channel_context = msg.channel.build_message_context()
        elif contact:
            message_context = dict(__default__='', contact=contact_context)
        else:
            message_context = dict(__default__='')

        # If we still don't know our channel and have a contact, derive the right channel to use
        if not channel_context and contact:
            _contact, contact_urn = Msg.resolve_recipient(self.org, self.created_by, contact, None)

            # only populate channel if this contact can actually be reached (ie, has a URN)
            if contact_urn:
                channel = contact.org.get_send_channel(contact_urn=contact_urn)
                channel_context = channel.build_message_context() if channel else None

        run = self.runs.filter(contact=contact).order_by('-created_on').first()
        run_context = run.field_dict() if run else {}

        # our current flow context
        flow_context = Flow.build_flow_context(self, contact, contact_context)

        context = dict(flow=flow_context, channel=channel_context, step=message_context, extra=run_context)

        # if we have parent or child contexts, add them in too
        if run:
            if run.parent:
                context['parent'] = Flow.build_flow_context(run.parent.flow, run.parent.contact)

            # see if we spawned any children and add them too
            child_run = FlowRun.objects.filter(parent=run).order_by('-created_on').first()
            if child_run:
                context['child'] = Flow.build_flow_context(child_run.flow, child_run.contact)

        if contact:
            context['contact'] = contact_context

        return context

    def get_results(self, contact=None, filter_ruleset=None, only_last_run=True, run=None):
        if filter_ruleset:
            ruleset_list = [filter_ruleset]
        elif run and hasattr(run.flow, 'ruleset_prefetch'):
            ruleset_list = run.flow.ruleset_prefetch
        else:
            ruleset_list = None

        (rulesets, rule_categories) = self.build_ruleset_caches(ruleset_list)

        # for each of the contacts that participated
        results = []

        if run:
            runs = [run]
            flow_steps = [s for s in run.steps.all() if s.rule_uuid]
        else:
            runs = self.runs.all().select_related('contact')

            # hide simulation test contact
            runs = runs.filter(contact__is_test=Contact.get_simulation())

            if contact:
                runs = runs.filter(contact=contact)

            runs = runs.order_by('contact', '-created_on')

            # or possibly only the last run
            if only_last_run:
                runs = runs.distinct('contact')

            flow_steps = FlowStep.objects.filter(step_uuid__in=rulesets.keys()).exclude(rule_uuid=None)

            # filter our steps to only the runs we care about
            flow_steps = flow_steps.filter(run__pk__in=[r.pk for r in runs])

            # and the ruleset we care about
            if filter_ruleset:
                flow_steps = flow_steps.filter(step_uuid=filter_ruleset.uuid)

            flow_steps = flow_steps.order_by('arrived_on', 'pk')
            flow_steps = flow_steps.select_related('run').prefetch_related('messages', 'broadcasts')

        steps_cache = {}
        for step in flow_steps:

            step_dict = dict(left_on=step.left_on,
                             arrived_on=step.arrived_on,
                             rule_uuid=step.rule_uuid,
                             rule_category=step.rule_category,
                             rule_decimal_value=step.rule_decimal_value,
                             rule_value=step.rule_value,
                             text=step.get_text(),
                             step_uuid=step.step_uuid)

            step_run = step.run.id

            if step_run in steps_cache.keys():
                steps_cache[step_run].append(step_dict)

            else:
                steps_cache[step_run] = [step_dict]

        for run in runs:
            first_seen = None
            last_seen = None
            values = []

            if run.id in steps_cache:
                run_steps = steps_cache[run.id]
            else:
                run_steps = []

            for rule_step in run_steps:
                ruleset = rulesets.get(rule_step['step_uuid'])
                if not first_seen:
                    first_seen = rule_step['left_on']
                last_seen = rule_step['arrived_on']

                if ruleset:
                    time = rule_step['left_on'] if rule_step['left_on'] else rule_step['arrived_on']

                    label = ruleset.label
                    category = rule_categories.get(rule_step['rule_uuid'], None)

                    # if this category no longer exists, use the category label at the time
                    if not category:
                        category = rule_step['rule_category']

                    value = rule_step['rule_decimal_value'] if rule_step['rule_decimal_value'] is not None else rule_step['rule_value']

                    values.append(dict(node=rule_step['step_uuid'],
                                       label=label,
                                       category=category,
                                       text=rule_step['text'],
                                       value=value,
                                       rule_value=rule_step['rule_value'],
                                       time=time))

            results.append(dict(contact=run.contact, values=values, first_seen=first_seen, last_seen=last_seen, run=run.pk))

        # sort so most recent is first
        now = timezone.now()
        results = sorted(results, reverse=True, key=lambda result: result['first_seen'] if result['first_seen'] else now)
        return results

    def async_start(self, user, groups, contacts, restart_participants=False):
        """
        Causes us to schedule a flow to start in a background thread.
        """
        from .tasks import start_flow_task

        # create a flow start object
        flow_start = FlowStart.objects.create(flow=self, restart_participants=restart_participants,
                                              created_by=user, modified_by=user)

        contact_ids = [c.id for c in contacts]
        flow_start.contacts.add(*contact_ids)

        group_ids = [g.id for g in groups]
        flow_start.groups.add(*group_ids)

        start_flow_task.delay(flow_start.pk)

    def start(self, groups, contacts, restart_participants=False, started_flows=None,
              start_msg=None, extra=None, flow_start=None, parent_run=None, interrupt=True):
        """
        Starts a flow for the passed in groups and contacts.
        """
        # build up querysets of our groups for memory efficiency
        if isinstance(groups, QuerySet):  # pragma: no cover
            group_qs = groups
        else:
            group_qs = ContactGroup.all_groups.filter(id__in=[g.id for g in groups])

        # build up querysets of our contacts for memory efficiency
        if isinstance(contacts, QuerySet):  # pragma: no cover
            contact_qs = contacts
        else:
            contact_qs = Contact.objects.filter(id__in=[c.id for c in contacts])

        self.ensure_current_version()

        if started_flows is None:
            started_flows = []

        # prevents infinite loops
        if self.pk in started_flows:
            return

        # add this flow to our list of started flows
        started_flows.append(self.pk)

        if not self.entry_uuid:
            return

        if start_msg and start_msg.id:
            start_msg.msg_type = FLOW
            start_msg.save(update_fields=['msg_type'])

        all_contact_ids = Contact.all().filter(Q(all_groups__in=group_qs) | Q(pk__in=contact_qs))
        all_contact_ids = all_contact_ids.only('is_test').order_by('pk').values_list('pk', flat=True).distinct('pk')

        if not restart_participants:
            # exclude anybody who has already participated in the flow
            already_started = set(self.runs.all().values_list('contact_id', flat=True))
            all_contact_ids = [contact_id for contact_id in all_contact_ids if contact_id not in already_started]

        # if we have a parent run, find any parents/grandparents that are active, we'll keep these active
        ancestor_ids = []
        ancestor = parent_run
        while ancestor:
            ancestor_ids.append(ancestor.id)
            ancestor = ancestor.parent

        # for the contacts that will be started, exit any existing flow runs
        active_runs = FlowRun.objects.filter(is_active=True, contact__pk__in=all_contact_ids).exclude(id__in=ancestor_ids)
        FlowRun.bulk_exit(active_runs, FlowRun.EXIT_TYPE_INTERRUPTED)

        # if we are interrupting parent flow runs, mark them as completed
        if ancestor_ids and interrupt:
            ancestor_runs = FlowRun.objects.filter(id__in=ancestor_ids)
            FlowRun.bulk_exit(ancestor_runs, FlowRun.EXIT_TYPE_COMPLETED)

        contact_count = len(all_contact_ids)

        # update our total flow count on our flow start so we can keep track of when it is finished
        if flow_start:
            flow_start.contact_count = contact_count
            flow_start.save(update_fields=['contact_count'])

        # if there are no contacts to start this flow, then update our status and exit this flow
        if contact_count == 0:
            if flow_start:
                flow_start.update_status()
            return

        # single contact starting from a trigger? increment our unread count
        if start_msg and contact_count == 1:
            if Contact.objects.filter(pk=all_contact_ids[0], org=self.org, is_test=False).first():
                self.increment_unread_responses()

        if self.flow_type == Flow.VOICE:
            return self.start_call_flow(all_contact_ids, start_msg=start_msg,
                                        extra=extra, flow_start=flow_start, parent_run=parent_run)

        else:
            return self.start_msg_flow(all_contact_ids,
                                       started_flows=started_flows, start_msg=start_msg,
                                       extra=extra, flow_start=flow_start, parent_run=parent_run)

    def start_call_flow(self, all_contact_ids, start_msg=None, extra=None, flow_start=None, parent_run=None):
        from temba.ivr.models import IVRCall
        runs = []
        channel = self.org.get_call_channel()

        if not channel or Channel.ROLE_CALL not in channel.role:
            return runs

        for contact_id in all_contact_ids:
            contact = Contact.objects.filter(pk=contact_id, org=channel.org).first()
            contact_urn = contact.get_urn(TEL_SCHEME)
            channel = self.org.get_call_channel(contact_urn=contact_urn)

            # can't reach this contact, move on
            if not contact or not contact_urn or not channel:  # pragma: no cover
                continue

            run = FlowRun.create(self, contact_id, start=flow_start, parent=parent_run)
            if extra:
                run.update_fields(extra)

            # create our call objects
            call = IVRCall.create_outgoing(channel, contact, contact_urn, self, self.created_by)

            # save away our created call
            run.call = call
            run.save(update_fields=['call'])

            # if we were started by other call, save that off
            if parent_run and parent_run.call:
                call.parent = parent_run.call
                call.save()
            else:
                # trigger the call to start (in the background)
                call.start_call()

            runs.append(run)

        if flow_start:
            flow_start.update_status()

        return runs

    def start_msg_flow(self, all_contact_ids, started_flows=None, start_msg=None, extra=None,
                       flow_start=None, parent_run=None):

        start_msg_id = start_msg.id if start_msg else None
        flow_start_id = flow_start.id if flow_start else None

        if started_flows is None:
            started_flows = []

        # create the broadcast for this flow
        send_actions = self.get_entry_send_actions()

        # for each send action, we need to create a broadcast, we'll group our created messages under these
        broadcasts = []
        for send_action in send_actions:
            message_text = self.get_localized_text(send_action.msg)

            # if we have localized versions, add those to our broadcast definition
            language_dict = None
            if isinstance(send_action.msg, dict):
                language_dict = json.dumps(send_action.msg)

            if message_text:
                broadcast = Broadcast.create(self.org, self.created_by, message_text, [],
                                             language_dict=language_dict)
                broadcast.update_contacts(all_contact_ids)

                # manually set our broadcast status to QUEUED, our sub processes will send things off for us
                broadcast.status = QUEUED
                broadcast.save(update_fields=['status'])

                # add it to the list of broadcasts in this flow start
                broadcasts.append(broadcast)

        # if there are fewer contacts than our batch size, do it immediately
        if len(all_contact_ids) < START_FLOW_BATCH_SIZE:
            return self.start_msg_flow_batch(all_contact_ids, broadcasts=broadcasts, started_flows=started_flows,
                                             start_msg=start_msg, extra=extra, flow_start=flow_start,
                                             parent_run=parent_run)

        # otherwise, create batches instead
        else:
            # for all our contacts, build up start sms batches
            task_context = dict(contacts=[], flow=self.pk, flow_start=flow_start_id,
                                started_flows=started_flows, broadcasts=[b.id for b in broadcasts], start_msg=start_msg_id, extra=extra)

            batch_contacts = task_context['contacts']
            for contact_id in all_contact_ids:
                batch_contacts.append(contact_id)

                if len(batch_contacts) >= START_FLOW_BATCH_SIZE:
                    print "Starting flow '%s' for batch of %d contacts" % (self.name, len(task_context['contacts']))
                    push_task(self.org, 'flows', 'start_msg_flow_batch', task_context)
                    batch_contacts = []
                    task_context['contacts'] = batch_contacts

            if batch_contacts:
                print "Starting flow '%s' for batch of %d contacts" % (self.name, len(task_context['contacts']))
                push_task(self.org, 'flows', 'start_msg_flow_batch', task_context)

            return []

    def start_msg_flow_batch(self, batch_contact_ids, broadcasts, started_flows, start_msg=None,
                             extra=None, flow_start=None, parent_run=None):

        simulation = False
        if len(batch_contact_ids) == 1:
            if Contact.objects.filter(pk=batch_contact_ids[0], org=self.org, is_test=True).first():
                simulation = True

        # these fields are the initial state for our flow run
        run_fields = None
        if extra:
            # we keep 1024 values in @extra for new flow runs because we might be passing the state
            (normalized_fields, count) = FlowRun.normalize_fields(extra, 1024)
            run_fields = json.dumps(normalized_fields)

        # create all our flow runs for this set of contacts at once
        batch = []
        now = timezone.now()

        for contact_id in batch_contact_ids:
            run = FlowRun.create(self, contact_id, fields=run_fields, start=flow_start, created_on=now,
                                 parent=parent_run, db_insert=False)
            batch.append(run)
        FlowRun.objects.bulk_create(batch)

        # build a map of contact to flow run
        run_map = dict()
        for run in FlowRun.objects.filter(contact__in=batch_contact_ids, flow=self, created_on=now):
            run_map[run.contact_id] = run
            if run.contact.is_test:
                ActionLog.create(run, '%s has entered the "%s" flow' % (run.contact.get_display(self.org, short=True), run.flow.name))

        # update our expiration date on our runs, we do this by calculating it on one run then updating all others
        run.update_expiration(timezone.now())
        FlowRun.objects.filter(contact__in=batch_contact_ids, created_on=now).update(expires_on=run.expires_on,
                                                                                     modified_on=timezone.now())

        # if we have some broadcasts to optimize for
        message_map = dict()
        if broadcasts:
            # create our message context
            message_context_base = self.build_message_context(None, start_msg)
            if extra:
                message_context_base['extra'] = extra

            # and add each contact and message to each broadcast
            for broadcast in broadcasts:
                # create our message context
                message_context = dict()
                message_context.update(message_context_base)

                # provide the broadcast with a partial recipient list
                partial_recipients = list(), Contact.objects.filter(org=self.org, pk__in=batch_contact_ids)

                # create the sms messages
                created_on = timezone.now()
                broadcast.send(message_context=message_context, trigger_send=False,
                               response_to=start_msg, status=INITIALIZING, msg_type=FLOW,
                               created_on=created_on, base_language=self.base_language,
                               partial_recipients=partial_recipients, run_map=run_map)

                # map all the messages we just created back to our contact
                for msg in Msg.current_messages.filter(broadcast=broadcast, created_on=created_on):
                    if msg.contact_id not in message_map:
                        message_map[msg.contact_id] = [msg]
                    else:
                        message_map[msg.contact_id].append(msg)

        # now execute our actual flow steps
        (entry_actions, entry_rules) = (None, None)
        if self.entry_type == Flow.ACTIONS_ENTRY:
            entry_actions = ActionSet.objects.filter(uuid=self.entry_uuid).first()

        elif self.entry_type == Flow.RULES_ENTRY:
            entry_rules = RuleSet.objects.filter(uuid=self.entry_uuid).first()

        runs = []
        msgs = []
        optimize_sending_action = len(broadcasts) > 0

        for contact_id in batch_contact_ids:
            # each contact maintains its own list of started flows
            started_flows_by_contact = list(started_flows)

            run = run_map[contact_id]
            run_msgs = message_map.get(contact_id, [])
            arrived_on = timezone.now()

            if entry_actions:
                run_msgs += entry_actions.execute_actions(run, start_msg, started_flows_by_contact,
                                                          skip_leading_reply_actions=not optimize_sending_action)

                step = self.add_step(run, entry_actions, run_msgs, is_start=True, arrived_on=arrived_on)

                # and onto the destination
                if entry_actions.destination:
                    destination = Flow.get_node(entry_actions.flow,
                                                entry_actions.destination,
                                                entry_actions.destination_type)

                    next_step = self.add_step(run, destination, previous_step=step, arrived_on=timezone.now())

                    msg = Msg(org=self.org, contact_id=contact_id, text='', id=0)
                    handled, step_msgs = Flow.handle_destination(destination, next_step, run, msg, started_flows_by_contact,
                                                                 is_test_contact=simulation, trigger_send=False)
                    run_msgs += step_msgs

                else:
                    run.set_completed(final_step=step)

            elif entry_rules:
                step = self.add_step(run, entry_rules, run_msgs, is_start=True, arrived_on=arrived_on)

                # if we have a start message, go and handle the rule
                if start_msg:
                    Flow.find_and_handle(start_msg, started_flows_by_contact, triggered_start=True)

                # if we didn't get an incoming message, see if we need to evaluate it passively
                elif not entry_rules.is_pause() or entry_rules.is_ussd():
                    # create an empty placeholder message
                    msg = Msg(org=self.org, contact_id=contact_id, text='', id=0)
                    handled, step_msgs = Flow.handle_destination(entry_rules, step, run, msg, started_flows_by_contact, trigger_send=False)
                    run_msgs += step_msgs

            if start_msg:
                step.add_message(start_msg)

            runs.append(run)

            # add these messages as ones that are ready to send
            for msg in run_msgs:
                msgs.append(msg)

        # trigger our messages to be sent
        if msgs:
            # then send them off
            msgs.sort(key=lambda message: (message.contact_id, message.created_on))
            Msg.all_messages.filter(id__in=[m.id for m in msgs]).update(status=PENDING)

            # trigger a sync
            self.org.trigger_send(msgs)

        # if we have a flow start, check whether we are complete
        if flow_start:
            flow_start.update_status()

        return runs

    def add_step(self, run, node,
                 msgs=None, rule=None, category=None, call=None, is_start=False, previous_step=None, arrived_on=None):
        if msgs is None:
            msgs = []

        if not arrived_on:
            arrived_on = timezone.now()

        # update our timeouts
        timeout = node.get_timeout() if isinstance(node, RuleSet) else None
        run.update_timeout(arrived_on, timeout)

        if not is_start:
            # mark any other states for this contact as evaluated, contacts can only be in one place at time
            self.steps().filter(run=run, left_on=None).update(left_on=arrived_on, next_uuid=node.uuid,
                                                              rule_uuid=rule, rule_category=category)

        # then add our new step and associate it with our message
        step = FlowStep.objects.create(run=run, contact=run.contact, step_type=node.get_step_type(),
                                       step_uuid=node.uuid, arrived_on=arrived_on)

        # for each message, associate it with this step and set the label on it
        for msg in msgs:
            step.add_message(msg)

        # update the activity for our run
        if not run.contact.is_test:
            self.update_activity(step, previous_step, rule_uuid=rule)

        return step

    def remove_active_for_run_ids(self, run_ids):
        """
        Bulk deletion of activity for a list of run ids. This removes the runs
        from the active step, but does not remove the visited (path) data
        for the runs.
        """
        r = get_redis_connection()
        if run_ids:
            for key in self.calculate_active_step_keys():
                # remove keys 1,000 at a time
                for batch in chunk_list(run_ids, 1000):
                    r.srem(key, *batch)

    def remove_active_for_step(self, step):
        """
        Removes the active stat for a run at the given step, but does not
        remove the (path) data for the runs.
        """
        r = get_redis_connection()
        r.srem(self.get_stats_cache_key(FlowStatsCache.step_active_set, step.step_uuid), step.run.pk)

    def remove_visits_for_step(self, step):
        """
        Decrements the count for the given step
        """
        r = get_redis_connection()
        step_uuid = step.step_uuid
        if step.rule_uuid:
            step_uuid = step.rule_uuid
        r.hincrby(self.get_stats_cache_key(FlowStatsCache.visit_count_map), "%s:%s" % (step_uuid, step.next_uuid), -1)

    def update_activity(self, step, previous_step=None, rule_uuid=None):
        """
        Updates our cache for the given step. This will mark the current active step and
        record history path data for activity.

        :param step: the step they just took
        :param previous_step: the step they were just on
        :param rule_uuid: the uuid for the rule they came from (if any)
        :param simulation: if we are part of a simulation
        """

        with self.lock_on(FlowLock.activity):
            r = get_redis_connection()

            # remove our previous active spot
            if previous_step:
                self.remove_active_for_step(previous_step)

                # mark our path
                previous_uuid = previous_step.step_uuid

                # if we came from a rule, use that instead of our step
                if rule_uuid:
                    previous_uuid = rule_uuid
                r.hincrby(self.get_stats_cache_key(FlowStatsCache.visit_count_map), "%s:%s" % (previous_uuid, step.step_uuid), 1)

            # make us active on our new step
            r.sadd(self.get_stats_cache_key(FlowStatsCache.step_active_set, step.step_uuid), step.run.pk)

    def get_entry_send_actions(self):
        """
        Returns all the entry actions (the first actions in a flow) that are reply actions. This is used
        for grouping all our outgoing messages into a single Broadcast.
        """
        if not self.entry_uuid or self.entry_type != Flow.ACTIONS_ENTRY:
            return []

        # get our entry actions
        entry_actions = ActionSet.objects.filter(uuid=self.entry_uuid).first()
        send_actions = []

        if entry_actions:
            actions = entry_actions.get_actions()

            for action in actions:
                # if this isn't a reply action, bail, they might be modifying the contact
                if not isinstance(action, ReplyAction):
                    break

                send_actions.append(action)

        return send_actions

    def get_dependencies(self, dependencies=None):

        # need to make sure we have the latest version to inspect dependencies
        self.ensure_current_version()

        if not dependencies:
            dependencies = dict(flows=set(), groups=set(), campaigns=set(), triggers=set())

        flows = set()
        groups = set()

        # find all the flows we reference, note this won't include archived flows
        for action_set in self.action_sets.all():
            for action in action_set.get_actions():
                if hasattr(action, 'flow'):
                    flows.add(action.flow)
                if hasattr(action, 'groups'):
                    for group in action.groups:
                        if not isinstance(group, unicode):
                            groups.add(group)

        for ruleset in self.rule_sets.all():
            if ruleset.ruleset_type == RuleSet.TYPE_SUBFLOW:
                flow = Flow.objects.filter(uuid=ruleset.config_json()['flow']['uuid']).first()
                if flow:
                    flows.add(flow)

        # add any campaigns that use our groups
        from temba.campaigns.models import Campaign
        campaigns = set(Campaign.objects.filter(org=self.org, group__in=groups, is_archived=False, is_active=True))
        for campaign in campaigns:
            flows.update(list(campaign.get_flows()))

        # and any of our triggers that reference us
        from temba.triggers.models import Trigger
        triggers = set(Trigger.objects.filter(org=self.org, flow=self, is_archived=False, is_active=True))

        dependencies['flows'].update(flows)
        dependencies['groups'].update(groups)
        dependencies['campaigns'].update(campaigns)
        dependencies['triggers'].update(triggers)

        if self in dependencies['flows']:
            return dependencies

        for flow in flows:
            dependencies = flow.get_dependencies(dependencies)

        return dependencies

    def as_json(self, expand_contacts=False):
        """
        Returns the JSON definition for this flow.

          expand_contacts:
            Add names for contacts and groups that are just ids. This is useful for human readable
            situations such as the flow editor.

        """
        flow = dict()

        if self.entry_uuid:
            flow[Flow.ENTRY] = self.entry_uuid
        else:
            flow[Flow.ENTRY] = None

        actionsets = []
        for actionset in ActionSet.objects.filter(flow=self).order_by('pk'):
            actionsets.append(actionset.as_json())

        def lookup_action_contacts(action, contacts, groups):

            if 'contact' in action:
                contacts.append(action['contact']['uuid'])

            if 'contacts' in action:
                for contact in action['contacts']:
                    contacts.append(contact['uuid'])

            if 'group' in action:
                g = action['group']
                if isinstance(g, dict):
                    if 'uuid' in g:
                        groups.append(g['uuid'])

            if 'groups' in action:
                for group in action['groups']:
                    if isinstance(group, dict):
                        if 'uuid' in group:
                            groups.append(group['uuid'])

        def replace_action_contacts(action, contacts, groups):

            if 'contact' in action:
                contact = contacts.get(action['contact']['uuid'], None)
                if contact:
                    action['contact'] = contact.as_json()

            if 'contacts' in action:
                expanded_contacts = []
                for contact in action['contacts']:
                    contact = contacts.get(contact['uuid'], None)
                    if contact:
                        expanded_contacts.append(contact.as_json())

                action['contacts'] = expanded_contacts

            if 'group' in action:
                # variable substitution
                group = action['group']
                if isinstance(group, dict):
                    if 'uuid' in group:
                        group = groups.get(group['uuid'], None)
                        if group:
                            action['group'] = dict(uuid=group.uuid, name=group.name)

            if 'groups' in action:
                expanded_groups = []
                for group in action['groups']:

                    # variable substitution
                    if not isinstance(group, dict):
                        expanded_groups.append(group)
                    else:
                        group_instance = groups.get(group['uuid'], None)
                        if group_instance:
                            expanded_groups.append(dict(uuid=group_instance.uuid, name=group_instance.name))
                        else:
                            expanded_groups.append(group)

                action['groups'] = expanded_groups

        if expand_contacts:
            groups = []
            contacts = []

            for actionset in actionsets:
                for action in actionset['actions']:
                    lookup_action_contacts(action, contacts, groups)

            # load them all
            contacts = dict((_.uuid, _) for _ in Contact.all().filter(org=self.org, uuid__in=contacts))
            groups = dict((_.uuid, _) for _ in ContactGroup.user_groups.filter(org=self.org, uuid__in=groups))

            # and replace them
            for actionset in actionsets:
                for action in actionset['actions']:
                    replace_action_contacts(action, contacts, groups)

        flow[Flow.ACTION_SETS] = actionsets

        # add in our rulesets
        rulesets = []
        for ruleset in RuleSet.objects.filter(flow=self).order_by('pk'):
            rulesets.append(ruleset.as_json())
        flow[Flow.RULE_SETS] = rulesets

        # required flow running details
        flow[Flow.BASE_LANGUAGE] = self.base_language
        flow[Flow.FLOW_TYPE] = self.flow_type
        flow[Flow.VERSION] = CURRENT_EXPORT_VERSION
        flow[Flow.METADATA] = self.get_metadata()
        return flow

    def get_metadata(self):

        metadata = dict()
        if self.metadata:
            metadata = json.loads(self.metadata)

        revision = self.revisions.all().order_by('-revision').first()

        metadata[Flow.NAME] = self.name
        metadata[Flow.SAVED_ON] = datetime_to_str(self.saved_on)
        metadata[Flow.REVISION] = revision.revision if revision else 1
        metadata[Flow.UUID] = self.uuid
        metadata[Flow.EXPIRES] = self.expires_after_minutes

        return metadata

    @classmethod
    def detect_invalid_cycles(cls, json_dict):
        """
        Checks for invalid cycles in our flow
        :param json_dict: our flow definition
        :return: invalid cycle path as list of uuids if found, otherwise empty list
        """

        # Adapted from a blog post by Guido:
        # http://neopythonic.blogspot.com/2009/01/detecting-cycles-in-directed-graph.html

        # Maintain path as a a depth-first path in the implicit tree;
        # path is represented as an OrderedDict of {node: [child,...]} pairs.

        nodes = list()
        node_map = {}

        for ruleset in json_dict.get(Flow.RULE_SETS, []):
            nodes.append(ruleset.get('uuid'))
            node_map[ruleset.get('uuid')] = ruleset

        for actionset in json_dict.get(Flow.ACTION_SETS, []):
            nodes.append(actionset.get('uuid'))
            node_map[actionset.get('uuid')] = actionset

        def get_destinations(uuid):
            node = node_map.get(uuid)

            if not node:
                return []

            rules = node.get('rules', [])
            destinations = []
            if rules:

                if node.get('ruleset_type', None) in RuleSet.TYPE_WAIT:
                    return []

                for rule in rules:
                    if rule.get('destination'):
                        destinations.append(rule.get('destination'))

            elif node.get('destination'):
                destinations.append(node.get('destination'))
            return destinations

        while nodes:
            root = nodes.pop()
            path = OrderedDict({root: get_destinations(root)})
            while path:
                # children at the fringe of the tree
                children = path[next(reversed(path))]
                while children:
                    child = children.pop()

                    # found a loop
                    if child in path:
                        pathlist = list(path)
                        return pathlist[pathlist.index(child):] + [child]

                    # new path
                    if child in nodes:
                        path[child] = get_destinations(child)
                        nodes.remove(child)
                        break
                else:
                    # no more children; pop back up a level
                    path.popitem()
        return None

    def ensure_current_version(self):
        """
        Makes sure the flow is at the current version. If it isn't it will
        migrate the definition forward updating the flow accordingly.
        """
        if self.version_number < CURRENT_EXPORT_VERSION:
            with self.lock_on(FlowLock.definition):
                revision = self.revisions.all().order_by('-revision').all().first()
                if revision:
                    json_flow = revision.get_definition_json()
                else:
                    json_flow = self.as_json()

                self.update(json_flow)
                self.refresh_from_db()

    def update(self, json_dict, user=None, force=False):
        """
        Updates a definition for a flow.
        """

        def get_step_type(dest, rulesets, actionsets):
            if dest:
                if rulesets.get(dest, None):
                    return FlowStep.TYPE_RULE_SET
                if actionsets.get(dest, None):
                    return FlowStep.TYPE_ACTION_SET
            return None

        cycle = Flow.detect_invalid_cycles(json_dict)
        if cycle:
            raise FlowException("Found invalid cycle: %s" % cycle)

        try:
            # check whether the flow has changed since this flow was last saved
            if user and not force:
                saved_on = json_dict.get(Flow.METADATA).get(Flow.SAVED_ON, None)
                org = user.get_org()
                tz = org.get_tzinfo()

                if not saved_on or str_to_datetime(saved_on, tz) < self.saved_on:
                    saver = ""
                    if self.saved_by.first_name:
                        saver += "%s " % self.saved_by.first_name
                    if self.saved_by.last_name:
                        saver += "%s" % self.saved_by.last_name

                    if not saver:
                        saver = self.saved_by.username

                    return dict(status="unsaved", description="Flow NOT Saved", saved_on=datetime_to_str(self.saved_on), saved_by=saver)

            top_y = 0
            top_uuid = None

            # load all existing objects into dicts by uuid
            existing_actionsets = dict()
            for actionset in self.action_sets.all():
                existing_actionsets[actionset.uuid] = actionset

            existing_rulesets = dict()
            for ruleset in self.rule_sets.all():
                existing_rulesets[ruleset.uuid] = ruleset

            # set of uuids which we've seen, we use this set to remove objects no longer used in this flow
            seen = set()
            destinations = set()

            # our steps in our current update submission
            current_actionsets = {}
            current_rulesets = {}

            # parse our actions
            for actionset in json_dict.get(Flow.ACTION_SETS, []):

                uuid = actionset.get(Flow.UUID)

                # validate our actions, normalizing them as JSON after reading them
                actions = [_.as_json() for _ in Action.from_json_array(self.org, actionset.get(Flow.ACTIONS))]

                if actions:
                    current_actionsets[uuid] = actions

            for ruleset in json_dict.get(Flow.RULE_SETS, []):
                uuid = ruleset.get(Flow.UUID)
                current_rulesets[uuid] = ruleset
                seen.add(uuid)

            # create all our rule sets
            for ruleset in json_dict.get(Flow.RULE_SETS, []):

                uuid = ruleset.get(Flow.UUID)
                rules = ruleset.get(Flow.RULES)
                label = ruleset.get(Flow.LABEL, None)
                operand = ruleset.get(Flow.OPERAND, None)
                finished_key = ruleset.get(Flow.FINISHED_KEY)
                ruleset_type = ruleset.get(Flow.RULESET_TYPE)
                config = ruleset.get(Flow.CONFIG)

                if not config:
                    config = dict()

                # cap our lengths
                if label:
                    label = label[:64]

                if operand:
                    operand = operand[:128]

                (x, y) = (ruleset.get(Flow.X), ruleset.get(Flow.Y))

                if not top_uuid or y < top_y:
                    top_y = y
                    top_uuid = uuid

                # validate we can parse our rules, this will throw if not
                Rule.from_json_array(self.org, rules)

                for rule in rules:
                    if 'destination' in rule:
                        # if the destination was excluded for not having any actions
                        # remove the connection for our rule too
                        if rule['destination'] not in current_actionsets and rule['destination'] not in seen:
                            rule['destination'] = None
                        else:
                            destination_uuid = rule.get('destination', None)
                            destinations.add(destination_uuid)

                            # determine what kind of destination we are pointing to
                            rule['destination_type'] = get_step_type(destination_uuid,
                                                                     current_rulesets, current_actionsets)

                            # print "Setting destination [%s] type to: %s" % (destination_uuid, rule['destination_type'])

                existing = existing_rulesets.get(uuid, None)

                if existing:
                    existing.label = ruleset.get(Flow.LABEL, None)
                    existing.set_rules_dict(rules)
                    existing.operand = operand
                    existing.label = label
                    existing.finished_key = finished_key
                    existing.ruleset_type = ruleset_type
                    existing.set_config(config)
                    (existing.x, existing.y) = (x, y)
                    existing.save()
                else:

                    existing = RuleSet.objects.create(flow=self,
                                                      uuid=uuid,
                                                      label=label,
                                                      rules=json.dumps(rules),
                                                      finished_key=finished_key,
                                                      ruleset_type=ruleset_type,
                                                      operand=operand,
                                                      config=json.dumps(config),
                                                      x=x, y=y)

                existing_rulesets[uuid] = existing

                # update our value type based on our new rules
                existing.value_type = existing.get_value_type()
                RuleSet.objects.filter(pk=existing.pk).update(value_type=existing.value_type)

            # now work through our action sets
            for actionset in json_dict.get(Flow.ACTION_SETS, []):
                uuid = actionset.get(Flow.UUID)

                # skip actionsets without any actions. This happens when there are no valid
                # actions in an actionset such as when deleted groups or flows are the only actions
                if uuid not in current_actionsets:
                    continue

                actions = current_actionsets[uuid]
                seen.add(uuid)

                (x, y) = (actionset.get(Flow.X), actionset.get(Flow.Y))

                if not top_uuid or y < top_y:
                    top_y = y
                    top_uuid = uuid

                existing = existing_actionsets.get(uuid, None)

                # lookup our destination
                destination_uuid = actionset.get('destination')
                destination_type = get_step_type(destination_uuid, current_rulesets, current_actionsets)

                if destination_uuid:
                    if not destination_type:
                        destination_uuid = None

                # only create actionsets if there are actions
                if actions:
                    if existing:
                        # print "Updating %s to point to %s" % (unicode(actions), destination_uuid)
                        existing.destination = destination_uuid
                        existing.destination_type = destination_type
                        existing.set_actions_dict(actions)
                        (existing.x, existing.y) = (x, y)
                        existing.save()
                    else:
                        existing = ActionSet.objects.create(flow=self,
                                                            uuid=uuid,
                                                            destination=destination_uuid,
                                                            destination_type=destination_type,
                                                            actions=json.dumps(actions),
                                                            x=x, y=y)

                        existing_actionsets[uuid] = existing

            # now work through all our objects once more, making sure all uuids map appropriately
            for existing in existing_actionsets.values():
                if existing.uuid not in seen:
                    del existing_actionsets[existing.uuid]
                    existing.delete()

            for existing in existing_rulesets.values():
                if existing.uuid not in seen:
                    # clean up any values on this ruleset
                    Value.objects.filter(ruleset=existing, org=self.org).delete()

                    del existing_rulesets[existing.uuid]
                    existing.delete()

            # make sure all destinations are present though
            for destination in destinations:
                if destination not in existing_rulesets and destination not in existing_actionsets:
                    raise FlowException("Invalid destination: '%s', no matching actionset or ruleset" % destination)

            entry = json_dict.get('entry', None)

            # check if we are pointing to a destination that is no longer valid
            if entry not in existing_rulesets and entry not in existing_actionsets:
                entry = None

            if not entry and top_uuid:
                entry = top_uuid

            # set our entry
            if entry in existing_actionsets:
                self.entry_uuid = entry
                self.entry_type = Flow.ACTIONS_ENTRY
            elif entry in existing_rulesets:
                self.entry_uuid = entry
                self.entry_type = Flow.RULES_ENTRY
            else:
                self.entry_uuid = None
                self.entry_type = None

            # if we have a base language, set that
            self.base_language = json_dict.get('base_language', None)

            # set our metadata
            self.metadata = None
            if Flow.METADATA in json_dict:
                self.metadata = json.dumps(json_dict[Flow.METADATA])

            if user:
                self.saved_by = user
            self.saved_on = timezone.now()
            self.version_number = CURRENT_EXPORT_VERSION
            self.save()

            # clear property cache
            self.clear_props_cache()

            # create a version of our flow for posterity
            if user is None:
                user = self.created_by

            # last version
            revision = 1
            last_revision = self.revisions.order_by('-revision').first()
            if last_revision:
                revision = last_revision.revision + 1

            # create a new version
            self.revisions.create(definition=json.dumps(json_dict),
                                  created_by=user,
                                  modified_by=user,
                                  spec_version=CURRENT_EXPORT_VERSION,
                                  revision=revision)

            return dict(status="success", description="Flow Saved",
                        saved_on=datetime_to_str(self.saved_on), revision=revision)

        except Exception as e:
            # note that badness happened
            import logging
            logger = logging.getLogger(__name__)
            logger.exception(unicode(e))
            import traceback
            traceback.print_exc(e)
            raise e

    def __unicode__(self):
        return self.name

    class Meta:
        ordering = ('-modified_on',)


class FlowRun(models.Model):
    EXIT_TYPE_COMPLETED = 'C'
    EXIT_TYPE_INTERRUPTED = 'I'
    EXIT_TYPE_EXPIRED = 'E'
    EXIT_TYPE_CHOICES = ((EXIT_TYPE_COMPLETED, _("Completed")),
                         (EXIT_TYPE_INTERRUPTED, _("Interrupted")),
                         (EXIT_TYPE_EXPIRED, _("Expired")))

    INVALID_EXTRA_KEY_CHARS = re.compile(r'[^a-zA-Z0-9_]')

    org = models.ForeignKey(Org, related_name='runs', db_index=False)

    flow = models.ForeignKey(Flow, related_name='runs')

    contact = models.ForeignKey(Contact, related_name='runs')

    call = models.ForeignKey('ivr.IVRCall', related_name='runs', null=True, blank=True,
                             help_text=_("The call that handled this flow run, only for voice flows"))

    is_active = models.BooleanField(default=True,
                                    help_text=_("Whether this flow run is currently active"))

    fields = models.TextField(blank=True, null=True,
                              help_text=_("A JSON representation of any custom flow values the user has saved away"))

    created_on = models.DateTimeField(default=timezone.now,
                                      help_text=_("When this flow run was created"))

    modified_on = models.DateTimeField(auto_now=True,
                                       help_text=_("When this flow run was last updated"))

    exited_on = models.DateTimeField(null=True,
                                     help_text=_("When the contact exited this flow run"))

    exit_type = models.CharField(null=True, max_length=1, choices=EXIT_TYPE_CHOICES,
                                 help_text=_("Why the contact exited this flow run"))

    expires_on = models.DateTimeField(null=True,
                                      help_text=_("When this flow run will expire"))

    timeout_on = models.DateTimeField(null=True,
                                      help_text=_("When this flow will next time out (if any)"))

    responded = models.BooleanField(default=False, help_text='Whether contact has responded in this run')

    start = models.ForeignKey('flows.FlowStart', null=True, blank=True, related_name='runs',
                              help_text=_("The FlowStart objects that started this run"))

    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                     help_text="The user which submitted this flow run")

    parent = models.ForeignKey('flows.FlowRun', null=True, help_text=_("The parent run that triggered us"))

    @classmethod
    def create(cls, flow, contact_id, start=None, call=None, fields=None,
               created_on=None, db_insert=True, submitted_by=None, parent=None):

        args = dict(org=flow.org, flow=flow, contact_id=contact_id, start=start,
                    call=call, fields=fields, submitted_by=submitted_by, parent=parent)

        if created_on:
            args['created_on'] = created_on

        if db_insert:
            return FlowRun.objects.create(**args)
        else:
            return FlowRun(**args)

    @classmethod
    def normalize_field_key(cls, key):
        return FlowRun.INVALID_EXTRA_KEY_CHARS.sub('_', key)[:255]

    @classmethod
    def normalize_fields(cls, fields, max_values=128, count=-1):
        """
        Turns an arbitrary dictionary into a dictionary containing only string keys and values
        """
        if isinstance(fields, (str, unicode)):
            return fields[:640], count + 1

        elif isinstance(fields, numbers.Number):
            return fields, count + 1

        elif isinstance(fields, dict):
            count += 1
            field_dict = dict()
            for (k, v) in fields.items():
                (field_dict[FlowRun.normalize_field_key(k)], count) = FlowRun.normalize_fields(v, max_values, count)

                if count >= max_values:
                    break

            return field_dict, count

        elif isinstance(fields, list):
            count += 1
            list_dict = dict()
            for (i, v) in enumerate(fields):
                (list_dict[str(i)], count) = FlowRun.normalize_fields(v, max_values, count)

                if count >= max_values:
                    break

            return list_dict, count

        else:
            return unicode(fields), count + 1

    @classmethod
    def bulk_exit(cls, runs, exit_type, exited_on=None):
        """
        Exits (expires, interrupts) runs in bulk
        """
        if isinstance(runs, list):
            runs = [{'id': r.pk, 'flow_id': r.flow_id} for r in runs]
        else:
            runs = list(runs.values('id', 'flow_id'))  # select only what we need...

        # organize runs by flow
        runs_by_flow = defaultdict(list)
        for run in runs:
            runs_by_flow[run['flow_id']].append(run['id'])

        # for each flow, remove activity for all runs
        for flow_id, run_ids in runs_by_flow.iteritems():
            flow = Flow.objects.filter(id=flow_id).first()

            if flow:
                flow.remove_active_for_run_ids(run_ids)

        modified_on = timezone.now()
        if not exited_on:
            exited_on = modified_on

        from .tasks import continue_parent_flows

        # batch this for 1,000 runs at a time so we don't grab locks for too long
        for batch in chunk_list(runs, 1000):
            ids = [r['id'] for r in batch]
            run_objs = FlowRun.objects.filter(pk__in=ids)
            run_objs.update(is_active=False, exited_on=exited_on, exit_type=exit_type, modified_on=modified_on)

            # continue the parent flows to continue async
            continue_parent_flows.delay(ids)

    def get_last_msg(self, direction):
        """
        Returns the last incoming msg on this run, or an empty dummy message if there is none
        """
        msg = Msg.all_messages.filter(steps__run=self, direction=direction).order_by('-created_on').first()
        return msg

    @classmethod
    def continue_parent_flow_runs(cls, runs):
        """
        Hands flow control back to our parent run if we have one
        """
        runs = runs.filter(parent__flow__is_active=True, parent__flow__is_archived=False)
        for run in runs:
            steps = run.parent.steps.filter(left_on=None, step_type=FlowStep.TYPE_RULE_SET)
            step = steps.select_related('run', 'run__flow', 'run__contact', 'run__flow__org').first()

            if step:
                ruleset = RuleSet.objects.filter(uuid=step.step_uuid, ruleset_type=RuleSet.TYPE_SUBFLOW, flow__org=step.run.org).first()
                if ruleset:
                    # use the last incoming message on this step
                    msg = step.messages.filter(direction=INCOMING).order_by('-created_on').first()

                    # if we are routing back to the parent before a msg was sent, we need a placeholder
                    if not msg:
                        msg = Msg()
                        msg.text = ''
                        msg.org = run.org
                        msg.contact = run.contact

                    # finally, trigger our parent flow
                    Flow.find_and_handle(msg, started_flows=[run.flow, run.parent.flow], resume_parent_run=True)

    def resume_after_timeout(self):
        """
        Resumes a flow that is at a ruleset that has timed out
        """
        last_step = self.steps.order_by('-arrived_on').first()
        node = last_step.get_step()

        # only continue if we are at a ruleset with a timeout
        if isinstance(node, RuleSet) and timezone.now() > self.timeout_on > last_step.arrived_on:
            timeout = node.get_timeout()

            # if our current node doesn't have a timeout, then we've moved on
            if timeout:
                # get the last outgoing msg for this contact
                msg = self.get_last_msg(OUTGOING)

                # check that our last outgoing msg was sent and our timeout is in the past, otherwise reschedule
                if msg and (not msg.sent_on or timezone.now() < msg.sent_on + timedelta(minutes=timeout) - timedelta(seconds=5)):
                    self.update_timeout(msg.sent_on if msg.sent_on else timezone.now(), timeout)

                # look good, lets resume this run
                else:
                    msg = self.get_last_msg(INCOMING)
                    if not msg:
                        msg = Msg()
                        msg.text = ''
                        msg.org = self.org
                        msg.contact = self.contact
                    Flow.find_and_handle(msg, resume_after_timeout=True)

    def release(self):
        """
        Permanently deletes this flow run
        """
        # remove each of our steps. we do this one at a time
        # so we can decrement the activity properly
        for step in self.steps.all():
            step.release()

        # remove our run from the activity
        with self.flow.lock_on(FlowLock.activity):
            self.flow.remove_active_for_run_ids([self.pk])

        # lastly delete ourselves
        self.delete()

    def set_completed(self, final_step=None, completed_on=None):
        """
        Mark a run as complete
        """

        if self.contact.is_test:
            ActionLog.create(self, _('%s has exited this flow') % self.contact.get_display(self.flow.org, short=True))

        now = timezone.now()

        if not completed_on:
            completed_on = now

        # mark that we left this step
        if final_step:
            final_step.left_on = completed_on
            final_step.save(update_fields=['left_on'])
            self.flow.remove_active_for_step(final_step)

        # mark this flow as inactive
        self.exit_type = FlowRun.EXIT_TYPE_COMPLETED
        self.exited_on = completed_on
        self.modified_on = now
        self.is_active = False
        self.save(update_fields=('exit_type', 'exited_on', 'modified_on', 'is_active'))

        # let our parent know we finished
        from .tasks import continue_parent_flows
        continue_parent_flows.delay([self.pk])

    def set_interrupted(self, final_step=None):
        """
        Mark run as interrupted
        """
        if self.contact.is_test:
            ActionLog.create(self, _('%s has interrupted this flow') % self.contact.get_display(self.flow.org, short=True))

        now = timezone.now()

        if final_step:
            final_step.left_on = now
            final_step.save(update_fields=['left_on'])
            self.flow.remove_active_for_step(final_step)

        # mark this flow as inactive
        self.exit_type = FlowRun.EXIT_TYPE_INTERRUPTED
        self.exited_on = now
        self.modified_on = now
        self.is_active = False
        self.save(update_fields=('exit_type', 'exited_on', 'modified_on', 'is_active'))

    def update_timeout(self, now, minutes):
        """
        Updates our timeout for our run, either clearing it or setting it appropriately
        """
        if not minutes and self.timeout_on:
            self.timeout_on = None
            self.modified_on = now
            self.save(update_fields=['timeout_on', 'modified_on'])
        elif minutes:
            self.timeout_on = now + timedelta(minutes=minutes)
            self.modified_on = now
            self.save(update_fields=['timeout_on', 'modified_on'])

    def update_expiration(self, point_in_time):
        """
        Set our expiration according to the flow settings
        """
        if self.flow.expires_after_minutes:
            now = timezone.now()
            if not point_in_time:
                point_in_time = now
            self.expires_on = point_in_time + timedelta(minutes=self.flow.expires_after_minutes)
            self.modified_on = now

            # save our updated fields
            self.save(update_fields=['expires_on', 'modified_on'])

            # if it's in the past, just expire us now
            if self.expires_on < now:
                self.expire()

        # parent should always have a later expiration than the children
        if self.parent:
            self.parent.update_expiration(self.expires_on)

    def expire(self):
        self.bulk_exit([self], FlowRun.EXIT_TYPE_EXPIRED)

    @classmethod
    def expire_all_for_contacts(cls, contacts):
        contact_runs = cls.objects.filter(is_active=True, contact__in=contacts)
        cls.bulk_exit(contact_runs, FlowRun.EXIT_TYPE_EXPIRED)

    def update_fields(self, field_map, max_values=128):
        # validate our field
        (field_map, count) = FlowRun.normalize_fields(field_map, max_values)

        if not self.fields:
            self.fields = json.dumps(field_map)
        else:
            existing_map = json.loads(self.fields)
            existing_map.update(field_map)
            self.fields = json.dumps(existing_map)

        self.save(update_fields=['fields'])

    def field_dict(self):
        return json.loads(self.fields) if self.fields else {}

    def is_completed(self):
        return self.exit_type == FlowRun.EXIT_TYPE_COMPLETED

    def is_interrupted(self):
        return self.exit_type == FlowRun.EXIT_TYPE_INTERRUPTED

    def create_outgoing_ivr(self, text, recording_url, response_to=None):

        # create a Msg object to track what happened
        from temba.msgs.models import DELIVERED, IVR

        media = None
        if recording_url:
            media = '%s/x-wav:%s' % (Msg.MEDIA_AUDIO, recording_url)
            text = recording_url

        msg = Msg.create_outgoing(self.flow.org, self.flow.created_by, self.contact, text, channel=self.call.channel,
                                  response_to=response_to, media=media,
                                  status=DELIVERED, msg_type=IVR)

        # play a recording or read some text
        if msg:
            if recording_url:
                self.voice_response.play(url=recording_url)
            else:
                self.voice_response.say(text)

        return msg


class FlowStep(models.Model):
    """
    A contact's visit to a node in a flow (rule set or action set)
    """
    TYPE_RULE_SET = 'R'
    TYPE_ACTION_SET = 'A'
    STEP_TYPE_CHOICES = ((TYPE_RULE_SET, "RuleSet"),
                         (TYPE_ACTION_SET, "ActionSet"))

    run = models.ForeignKey(FlowRun, related_name='steps')

    contact = models.ForeignKey(Contact, related_name='flow_steps')

    step_type = models.CharField(max_length=1, choices=STEP_TYPE_CHOICES, help_text=_("What type of node was visited"))

    step_uuid = models.CharField(max_length=36, db_index=True,
                                 help_text=_("The UUID of the ActionSet or RuleSet for this step"))

    rule_uuid = models.CharField(max_length=36, null=True,
                                 help_text=_("For uuid of the rule that matched on this ruleset, null on ActionSets"))

    rule_category = models.CharField(max_length=36, null=True,
                                     help_text=_("The category label that matched on this ruleset, null on ActionSets"))

    rule_value = models.CharField(max_length=640, null=True,
                                  help_text=_("The value that was matched in our category for this ruleset, null on ActionSets"))

    rule_decimal_value = models.DecimalField(max_digits=36, decimal_places=8, null=True,
                                             help_text=_("The decimal value that was matched in our category for this ruleset, null on ActionSets or if a non numeric rule was matched"))

    next_uuid = models.CharField(max_length=36, null=True,
                                 help_text=_("The uuid of the next step type we took"))

    arrived_on = models.DateTimeField(help_text=_("When the user arrived at this step in the flow"))

    left_on = models.DateTimeField(null=True, db_index=True,
                                   help_text=_("When the user left this step in the flow"))

    messages = models.ManyToManyField(Msg, related_name='steps',
                                      help_text=_("Any messages that are associated with this step (either sent or received)"))

    broadcasts = models.ManyToManyField(Broadcast, related_name='steps',
                                        help_text=_("Any broadcasts that are associated with this step (only sent)"))

    @classmethod
    def from_json(cls, json_obj, flow, run, previous_rule=None):

        node = json_obj['node']
        arrived_on = json_date_to_datetime(json_obj['arrived_on'])

        # find and update the previous step
        prev_step = FlowStep.objects.filter(run=run).order_by('-left_on').first()
        if prev_step:
            prev_step.left_on = arrived_on
            prev_step.next_uuid = node.uuid
            prev_step.save(update_fields=('left_on', 'next_uuid'))

        # generate the messages for this step
        msgs = []
        if node.is_ruleset():
            incoming = None
            if node.is_pause():
                # if a msg was sent to this ruleset, create it
                if json_obj['rule']:

                    media = None
                    if 'media' in json_obj['rule']:

                        media = json_obj['rule']['media']
                        (media_type, url) = media.split(':', 1)

                        # store the non-typed url in the value and text
                        json_obj['rule']['value'] = url
                        json_obj['rule']['text'] = url

                    # if we received a message
                    incoming = Msg.create_incoming(org=run.org, contact=run.contact, text=json_obj['rule']['text'],
                                                   media=media, msg_type=FLOW, status=HANDLED, date=arrived_on,
                                                   channel=None, urn=None)
            else:
                incoming = Msg.current_messages.filter(org=run.org, direction=INCOMING, steps__run=run).order_by('-pk').first()

            if incoming:
                msgs.append(incoming)
        else:
            actions = Action.from_json_array(flow.org, json_obj['actions'])

            last_incoming = Msg.all_messages.filter(org=run.org, direction=INCOMING, steps__run=run).order_by('-pk').first()

            for action in actions:
                msgs += action.execute(run, node.uuid, msg=last_incoming, offline_on=arrived_on)

        step = flow.add_step(run, node, msgs=msgs, previous_step=prev_step, arrived_on=arrived_on, rule=previous_rule)

        # if a rule was picked on this ruleset
        if node.is_ruleset() and json_obj['rule']:
            rule_uuid = json_obj['rule']['uuid']
            rule_value = json_obj['rule']['value']
            rule_category = json_obj['rule']['category']

            # update the value if we have an existing ruleset
            ruleset = RuleSet.objects.filter(flow=flow, uuid=node.uuid).first()
            if ruleset:
                rule = None
                for r in ruleset.get_rules():
                    if r.uuid == rule_uuid:
                        rule = r
                        break

                if not rule:
                    raise ValueError("No such rule with UUID %s" % rule_uuid)

                rule.category = rule_category
                ruleset.save_run_value(run, rule, rule_value)

            # update our step with our rule details
            step.rule_uuid = rule_uuid
            step.rule_category = rule_category
            step.rule_value = rule_value

            try:
                step.rule_decimal_value = Decimal(json_obj['rule']['value'])
            except Exception:
                pass

            step.save(update_fields=('rule_uuid', 'rule_category', 'rule_value', 'rule_decimal_value'))

        return step

    @classmethod
    def get_active_steps_for_contact(cls, contact, step_type=None):

        steps = FlowStep.objects.filter(run__is_active=True, run__flow__is_active=True, run__contact=contact, left_on=None)

        # don't consider voice steps, those are interactive
        steps = steps.exclude(run__flow__flow_type=Flow.VOICE)

        # real contacts don't deal with archived flows
        if not contact.is_test:
            steps = steps.filter(run__flow__is_archived=False)

        if step_type:
            steps = steps.filter(step_type=step_type)

        steps = steps.order_by('-pk')

        # optimize lookups
        return steps.select_related('run', 'run__flow', 'run__contact', 'run__flow__org')

    def release(self):
        if not self.contact.is_test:
            self.run.flow.remove_visits_for_step(self)

        # finally delete us
        self.delete()

    def save_rule_match(self, rule, value):
        self.rule_category = rule.category
        self.rule_uuid = rule.uuid

        if value is None:
            value = ''
        self.rule_value = unicode(value)[:640]

        if isinstance(value, Decimal):
            self.rule_decimal_value = value

        self.save(update_fields=['rule_category', 'rule_uuid', 'rule_value', 'rule_decimal_value'])

    def get_text(self, run=None):
        """
        Returns a single text value for this step. Since steps can have multiple outgoing messages, this isn't very
        useful but needed for backwards compatibility in API v1.
        """
        msg = self.messages.all().first()
        if msg:
            return msg.text

        # It's possible that messages have been purged but we still have broadcasts. Broadcast isn't implicitly ordered
        # like Msg is so .all().first() would cause an extra db hit even if all() has been prefetched.
        broadcasts = list(self.broadcasts.all())
        if broadcasts:
            run = run or self.run
            return broadcasts[0].get_translated_text(run.contact, base_language=run.flow.base_language, org=run.org)

        return None

    def add_message(self, msg):
        # no-op for no msg or mock msgs
        if not msg or not msg.id:
            return

        self.messages.add(msg)

        # if this msg is part of a broadcast, save that on our flowstep so we can later purge the msg
        if msg.broadcast:
            self.broadcasts.add(msg.broadcast)

        # incoming non-IVR messages won't have a type yet so update that
        if not msg.msg_type or msg.msg_type == INBOX:
            msg.msg_type = FLOW
            msg.save(update_fields=['msg_type'])

        # if message is from contact, mark run as responded
        if not self.run.responded and msg.direction == INCOMING:
            # update our local run's responded state and it's expiration
            self.run.responded = True

            # and make sure the db is up to date
            FlowRun.objects.filter(id=self.run.id, responded=False).update(responded=True)

    def get_step(self):
        """
        Returns either the RuleSet or ActionSet associated with this FlowStep
        """
        if self.step_type == FlowStep.TYPE_RULE_SET:
            return RuleSet.objects.filter(uuid=self.step_uuid).first()
        else:
            return ActionSet.objects.filter(uuid=self.step_uuid).first()

    def __unicode__(self):
        return "%s - %s:%s" % (self.run.contact, self.step_type, self.step_uuid)

    class Meta:
        index_together = ['step_uuid', 'next_uuid', 'rule_uuid', 'left_on']


class RuleSet(models.Model):
    TYPE_WAIT_MESSAGE = 'wait_message'

    # Ussd
    TYPE_WAIT_USSD_MENU = 'wait_menu'
    TYPE_WAIT_USSD = 'wait_ussd'

    # Calls
    TYPE_WAIT_RECORDING = 'wait_recording'
    TYPE_WAIT_DIGIT = 'wait_digit'
    TYPE_WAIT_DIGITS = 'wait_digits'

    # Surveys
    TYPE_WAIT_PHOTO = 'wait_photo'
    TYPE_WAIT_VIDEO = 'wait_video'
    TYPE_WAIT_AUDIO = 'wait_audio'
    TYPE_WAIT_GPS = 'wait_gps'

    TYPE_AIRTIME = 'airtime'
    TYPE_WEBHOOK = 'webhook'
    TYPE_RESTHOOK = 'resthook'
    TYPE_FLOW_FIELD = 'flow_field'
    TYPE_FORM_FIELD = 'form_field'
    TYPE_CONTACT_FIELD = 'contact_field'
    TYPE_EXPRESSION = 'expression'
    TYPE_RANDOM = 'random'
    TYPE_SUBFLOW = 'subflow'

    CONFIG_WEBHOOK = 'webhook'
    CONFIG_WEBHOOK_ACTION = 'webhook_action'
    CONFIG_RESTHOOK = 'resthook'

    TYPE_MEDIA = (TYPE_WAIT_PHOTO, TYPE_WAIT_GPS, TYPE_WAIT_VIDEO, TYPE_WAIT_AUDIO, TYPE_WAIT_RECORDING)

    TYPE_WAIT = (TYPE_WAIT_MESSAGE, TYPE_WAIT_RECORDING, TYPE_WAIT_DIGIT, TYPE_WAIT_DIGITS, TYPE_WAIT_USSD_MENU,
                 TYPE_WAIT_USSD, TYPE_WAIT_PHOTO, TYPE_WAIT_VIDEO, TYPE_WAIT_AUDIO, TYPE_WAIT_GPS)

    TYPE_USSD = (TYPE_WAIT_USSD_MENU, TYPE_WAIT_USSD)

    TYPE_CHOICES = ((TYPE_WAIT_MESSAGE, "Wait for message"),
                    (TYPE_WAIT_USSD_MENU, "Wait for USSD menu"),
                    (TYPE_WAIT_USSD, "Wait for USSD message"),
                    (TYPE_WAIT_RECORDING, "Wait for recording"),
                    (TYPE_WAIT_DIGIT, "Wait for digit"),
                    (TYPE_WAIT_DIGITS, "Wait for digits"),
                    (TYPE_SUBFLOW, "Subflow"),
                    (TYPE_WEBHOOK, "Webhook"),
                    (TYPE_RESTHOOK, "Resthook"),
                    (TYPE_AIRTIME, "Transfer Airtime"),
                    (TYPE_FORM_FIELD, "Split by message form"),
                    (TYPE_CONTACT_FIELD, "Split on contact field"),
                    (TYPE_EXPRESSION, "Split by expression"),
                    (TYPE_SUBFLOW, "Split Randomly"))

    uuid = models.CharField(max_length=36, unique=True)

    flow = models.ForeignKey(Flow, related_name='rule_sets')

    label = models.CharField(max_length=64, null=True, blank=True,
                             help_text=_("The label for this field"))

    operand = models.CharField(max_length=128, null=True, blank=True,
                               help_text=_("The value that rules will be run against, if None defaults to @step.value"))

    webhook_url = models.URLField(null=True, blank=True, max_length=255,
                                  help_text=_("The URL that will be called with the user's response before we run our rules"))

    webhook_action = models.CharField(null=True, blank=True, max_length=8, default='POST',
                                      help_text=_('How the webhook should be executed'))

    rules = models.TextField(help_text=_("The JSON encoded actions for this action set"))

    finished_key = models.CharField(max_length=1, null=True, blank=True,
                                    help_text="During IVR, this is the key to indicate we are done waiting")

    value_type = models.CharField(max_length=1, choices=Value.TYPE_CHOICES, default=Value.TYPE_TEXT,
                                  help_text="The type of value this ruleset saves")

    ruleset_type = models.CharField(max_length=16, choices=TYPE_CHOICES, null=True,
                                    help_text="The type of ruleset")

    response_type = models.CharField(max_length=1, help_text="The type of response that is being saved")

    config = models.TextField(null=True, verbose_name=_("Ruleset Configuration"),
                              help_text=_("RuleSet type specific configuration"))

    x = models.IntegerField()
    y = models.IntegerField()

    created_on = models.DateTimeField(auto_now_add=True, help_text=_("When this ruleset was originally created"))
    modified_on = models.DateTimeField(auto_now=True, help_text=_("When this ruleset was last modified"))

    @classmethod
    def get(cls, flow, uuid):
        return RuleSet.objects.filter(flow=flow, uuid=uuid).select_related('flow', 'flow__org').first()

    @classmethod
    def contains_step(cls, text):

        # remove any padding
        if text:
            text = text.strip()

        # match @step.value or @(step.value)
        return text and text[0] == '@' and 'step' in text

    def config_json(self):
        if not self.config:
            return dict()
        else:
            return json.loads(self.config)

    def set_config(self, config):
        self.config = json.dumps(config)

    def build_uuid_to_category_map(self):
        flow_language = self.flow.base_language

        uuid_to_category = dict()
        ordered_categories = []
        unique_categories = set()

        for rule in self.get_rules():
            label = rule.get_category_name(flow_language) if rule.category else unicode(_("Valid"))

            # ignore "Other" labels
            if label == "Other":
                continue

            # we only want to represent each unique label once
            if not label.lower() in unique_categories:
                unique_categories.add(label.lower())
                ordered_categories.append(dict(label=label, count=0))

            uuid_to_category[rule.uuid] = label

            # this takes care of results that were categorized with different rules that may not exist anymore
            for value in Value.objects.filter(ruleset=self, category=label).order_by('rule_uuid').distinct('rule_uuid'):
                uuid_to_category[value.rule_uuid] = label

        return ordered_categories, uuid_to_category

    def get_value_type(self):
        """
        Determines the value type that this ruleset will generate.
        """
        rules = self.get_rules()

        # we keep track of specialized rule types we see
        dec_rules = 0
        dt_rules = 0
        rule_count = 0

        for rule in self.get_rules():
            if not isinstance(rule, TrueTest):
                rule_count += 1

            if isinstance(rule, NumericTest):
                dec_rules += 1
            elif isinstance(rule, DateTest):
                dt_rules += 1

        # no real rules? this is open ended, return
        if rule_count == 0:
            return Value.TYPE_TEXT

        # if we are all of one type (excluding other) then we are that type
        if dec_rules == len(rules) - 1:
            return Value.TYPE_DECIMAL
        elif dt_rules == len(rules) - 1:
            return Value.TYPE_DATETIME
        else:
            return Value.TYPE_TEXT

    def get_voice_input(self, voice_response, action=None):

        # recordings aren't wrapped input they get tacked on at the end
        if self.ruleset_type == RuleSet.TYPE_WAIT_RECORDING:
            return voice_response
        elif self.ruleset_type == RuleSet.TYPE_WAIT_DIGITS:
            return voice_response.gather(finishOnKey=self.finished_key, timeout=60, action=action)
        else:
            # otherwise we assume it's single digit entry
            return voice_response.gather(numDigits=1, timeout=60, action=action)

    def is_pause(self):
        return self.ruleset_type in RuleSet.TYPE_WAIT

    def is_ussd(self):
        return self.ruleset_type in RuleSet.TYPE_USSD

    def get_timeout(self):
        for rule in self.get_rules():
            if isinstance(rule.test, TimeoutTest):
                return rule.test.minutes

        return None

    def find_matching_rule(self, step, run, msg, resume_after_timeout=False):
        orig_text = None
        if msg:
            orig_text = msg.text

        context = run.flow.build_message_context(run.contact, msg)

        if resume_after_timeout:
            for rule in self.get_rules():
                if isinstance(rule.test, TimeoutTest):
                    (result, value) = rule.matches(run, msg, context, orig_text)
                    if result > 0:
                        rule.category = run.flow.get_base_text(rule.category)
                        return rule, value

        elif self.ruleset_type in [RuleSet.TYPE_WEBHOOK, RuleSet.TYPE_RESTHOOK]:
            # figure out which URLs will be called
            if self.ruleset_type == RuleSet.TYPE_WEBHOOK:
                resthook = None
                urls = [self.config_json()[RuleSet.CONFIG_WEBHOOK]]
                action = self.config_json()[RuleSet.CONFIG_WEBHOOK_ACTION]

            elif self.ruleset_type == RuleSet.TYPE_RESTHOOK:
                from temba.api.models import Resthook

                # look up the rest hook
                resthook_slug = self.config_json()[RuleSet.CONFIG_RESTHOOK]
                resthook = Resthook.get_or_create(run.org, resthook_slug, run.flow.created_by)
                urls = resthook.get_subscriber_urls()

                # no urls? use None, as our empty case
                if not urls:
                    urls = [None]

                action = 'POST'

            # by default we are a failure (there are no resthooks for example)
            status_code = None
            body = ""

            for url in urls:
                from temba.api.models import WebHookEvent

                (value, errors) = Msg.substitute_variables(url, run.contact, context,
                                                           org=run.flow.org, url_encode=True)

                result = WebHookEvent.trigger_flow_event(value, self.flow, run, self,
                                                         run.contact, msg, action, resthook=resthook)

                # we haven't recorded any status yet, do so
                if not status_code:
                    status_code = result.status_code
                    body = result.body

                # our subscriber is no longer interested, remove this URL as a subscriber
                if result.status_code == 410:
                    resthook.remove_subscriber(url, run.flow.created_by)

                # if this is a success and we haven't ever succeeded, set our code and body
                elif 200 <= result.status_code < 300 and not (200 <= status_code < 300):
                    status_code = result.status_code
                    body = result.body

                # this was an empty URL, treat it as success regardless
                if url is None:
                    status_code = 200
                    body = _("No subscribers to this event")

            # default to a status code of 418 if we made no calls
            if not status_code:
                status_code = 418

            # find our matching rule, we pass in the status from our calls
            for rule in self.get_rules():
                (result, value) = rule.matches(run, msg, context, str(status_code))
                if result > 0:
                    rule.category = run.flow.get_base_text(rule.category)
                    return rule, body

        else:
            # if it's a form field, construct an expression accordingly
            if self.ruleset_type == RuleSet.TYPE_FORM_FIELD:
                config = self.config_json()
                delim = config.get('field_delimiter', ' ')
                self.operand = '@(FIELD(%s, %d, "%s"))' % (self.operand[1:], config.get('field_index', 0) + 1, delim)

            # if we have a custom operand, figure that out
            text = None
            if self.operand:
                (text, errors) = Msg.substitute_variables(self.operand, run.contact, context, org=run.flow.org)
            elif msg:
                text = msg.text

            if self.ruleset_type == RuleSet.TYPE_AIRTIME:

                # flow simulation will always simulate a suceessful airtime transfer
                # without saving the object in the DB
                if run.contact.is_test:
                    from temba.flows.models import ActionLog
                    log_txt = "Simulate Complete airtime transfer"
                    ActionLog.create(run, log_txt, safe=True)

                    airtime = AirtimeTransfer(status=AirtimeTransfer.SUCCESS)
                else:
                    airtime = AirtimeTransfer.trigger_airtime_event(self.flow.org, self, run.contact, msg)

                # rebuild our context again, the webhook may have populated something
                context = run.flow.build_message_context(run.contact, msg)

                # airtime test evaluate against the status of the airtime
                text = airtime.status

            try:
                rules = self.get_rules()
                for rule in rules:
                    (result, value) = rule.matches(run, msg, context, text)
                    if result > 0:
                        # treat category as the base category
                        rule.category = run.flow.get_base_text(rule.category)
                        return rule, value
            finally:
                if msg:
                    msg.text = orig_text

        return None, None

    def find_interrupt_rule(self, step, run, msg):
        rules = self.get_rules()
        for rule in rules:
            result, value = rule.matches(run, msg, {}, "")

            if result and value == "interrupted_status":
                return rule, value
        return None, None

    def save_run_value(self, run, rule, value):
        value = unicode(value)[:640]
        location_value = None
        dec_value = None
        dt_value = None
        media_value = None

        if isinstance(value, AdminBoundary):
            location_value = value
        else:
            dt_value = run.flow.org.parse_date(value)
            dec_value = run.flow.org.parse_decimal(value)

        # if its a media value, only store the path as the value
        if ':' in value:
            (media_type, media_path) = value.split(':', 1)
            if media_type in Msg.MEDIA_TYPES:
                media_value = value
                value = media_path

        # delete any existing values for this ruleset, run and contact, we only store the latest
        Value.objects.filter(contact=run.contact, run=run, ruleset=self).delete()

        Value.objects.create(contact=run.contact, run=run, ruleset=self, category=rule.category, rule_uuid=rule.uuid,
                             string_value=value, decimal_value=dec_value, datetime_value=dt_value,
                             location_value=location_value, media_value=media_value, org=run.flow.org)

        # invalidate any cache on this ruleset
        Value.invalidate_cache(ruleset=self)

    def get_step_type(self):
        return FlowStep.TYPE_RULE_SET

    def get_rules_dict(self):
        return json.loads(self.rules)

    def get_rules(self):
        return Rule.from_json_array(self.flow.org, json.loads(self.rules))

    def get_rule_uuids(self):
        return [rule['uuid'] for rule in json.loads(self.rules)]

    def set_rules_dict(self, json_dict):
        self.rules = json.dumps(json_dict)

    def set_rules(self, rules):
        rules_dict = []
        for rule in rules:
            rules_dict.append(rule.as_json())
        self.set_rules_dict(rules_dict)

    def as_json(self):
        ruleset_def = dict(uuid=self.uuid, x=self.x, y=self.y, label=self.label, rules=self.get_rules_dict(),
                           finished_key=self.finished_key, ruleset_type=self.ruleset_type, response_type=self.response_type,
                           operand=self.operand, config=self.config_json())

        # if we are pre-version 10, include our webhook and webhook_action in our dict
        if self.flow.version_number < 10:
            ruleset_def['webhook'] = self.webhook_url
            ruleset_def['webhook_action'] = self.webhook_action

        return ruleset_def

    def __unicode__(self):
        if self.label:
            return "RuleSet: %s - %s" % (self.uuid, self.label)
        else:
            return "RuleSet: %s" % (self.uuid, )


class ActionSet(models.Model):
    uuid = models.CharField(max_length=36, unique=True)
    flow = models.ForeignKey(Flow, related_name='action_sets')

    destination = models.CharField(max_length=36, null=True)
    destination_type = models.CharField(max_length=1, choices=FlowStep.STEP_TYPE_CHOICES, null=True)

    actions = models.TextField(help_text=_("The JSON encoded actions for this action set"))

    x = models.IntegerField()
    y = models.IntegerField()

    created_on = models.DateTimeField(auto_now_add=True, help_text=_("When this action was originally created"))
    modified_on = models.DateTimeField(auto_now=True, help_text=_("When this action was last modified"))

    @classmethod
    def get(cls, flow, uuid):
        return ActionSet.objects.filter(flow=flow, uuid=uuid).select_related('flow', 'flow__org').first()

    def get_step_type(self):
        return FlowStep.TYPE_ACTION_SET

    def execute_actions(self, run, msg, started_flows, skip_leading_reply_actions=True):
        actions = self.get_actions()
        msgs = []

        seen_other_action = False
        for action in actions:
            if not isinstance(action, ReplyAction):
                seen_other_action = True

            # if this is a reply action, we're skipping leading reply actions and we haven't seen other actions
            if not skip_leading_reply_actions and isinstance(action, ReplyAction) and not seen_other_action:
                # then skip it
                pass

            elif isinstance(action, StartFlowAction):
                if action.flow.pk in started_flows:
                    pass
                else:
                    msgs += action.execute(run, self.uuid, msg, started_flows)

                    # reload our contact and reassign it to our run, it may have been changed deep down in our child flow
                    run.contact = Contact.objects.get(pk=run.contact.pk)

            else:
                msgs += action.execute(run, self.uuid, msg)

                # actions modify the run.contact, update the msg contact in case they did so
                if msg:
                    msg.contact = run.contact

        return msgs

    def get_actions_dict(self):
        return json.loads(self.actions)

    def get_actions(self):
        return Action.from_json_array(self.flow.org, json.loads(self.actions))

    def set_actions_dict(self, json_dict):
        self.actions = json.dumps(json_dict)

    def as_json(self):
        return dict(uuid=self.uuid, x=self.x, y=self.y, destination=self.destination, actions=self.get_actions_dict())

    def __unicode__(self):  # pragma: no cover
        return "ActionSet: %s" % (self.uuid, )


class FlowRevision(SmartModel):
    """
    JSON definitions for previous flow revisions
    """
    flow = models.ForeignKey(Flow, related_name='revisions')

    definition = models.TextField(help_text=_("The JSON flow definition"))

    spec_version = models.IntegerField(default=CURRENT_EXPORT_VERSION, help_text=_("The flow version this definition is in"))

    revision = models.IntegerField(null=True, help_text=_("Revision number for this definition"))

    @classmethod
    def validate_flow_definition(cls, flow_spec):

        non_localized_error = _('Malformed flow, encountered non-localized definition')

        # should always have a base_language
        if 'base_language' not in flow_spec or not flow_spec['base_language']:
            raise ValueError(non_localized_error)

        # language should match values in definition
        base_language = flow_spec['base_language']

        def validate_localization(lang_dict):

            # must be a dict
            if not isinstance(lang_dict, dict):
                raise ValueError(non_localized_error)

            # and contain the base_language
            if base_language not in lang_dict:
                raise ValueError(non_localized_error)

        for actionset in flow_spec['action_sets']:
            for action in actionset['actions']:
                if 'msg' in action and action['type'] != 'email':
                    validate_localization(action['msg'])

        for ruleset in flow_spec['rule_sets']:
            for rule in ruleset['rules']:
                validate_localization(rule['category'])

    @classmethod
    def migrate_export(cls, org, exported_json, same_site, version, to_version=None):
        if not to_version:
            to_version = CURRENT_EXPORT_VERSION

        from temba.flows import flow_migrations
        while version < to_version and version < CURRENT_EXPORT_VERSION:

            migrate_fn = getattr(flow_migrations, 'migrate_export_to_version_%d' % (version + 1), None)
            if migrate_fn:
                exported_json = migrate_fn(exported_json, org, same_site)
            else:
                flows = []
                for json_flow in exported_json.get('flows', []):
                    migrate_fn = getattr(flow_migrations, 'migrate_to_version_%d' % (version + 1), None)
                    if migrate_fn:
                        json_flow = migrate_fn(json_flow, None)
                    flows.append(json_flow)
                exported_json['flows'] = flows
            version += 1

        return exported_json

    @classmethod
    def migrate_definition(cls, json_flow, flow, version, to_version=None):
        if not to_version:
            to_version = CURRENT_EXPORT_VERSION
        from temba.flows import flow_migrations
        while version < to_version and version < CURRENT_EXPORT_VERSION:
            migrate_fn = getattr(flow_migrations, 'migrate_to_version_%d' % (version + 1), None)
            if migrate_fn:
                json_flow = migrate_fn(json_flow, flow)
            version += 1

        return json_flow

    def get_definition_json(self):

        definition = json.loads(self.definition)

        # if it's previous to version 6, wrap the definition to
        # mirror our exports for those versions
        if self.spec_version <= 6:
            definition = dict(definition=definition, flow_type=self.flow.flow_type,
                              expires=self.flow.expires_after_minutes, id=self.flow.pk,
                              revision=self.revision, uuid=self.flow.uuid)

        # migrate our definition if necessary
        if self.spec_version < CURRENT_EXPORT_VERSION:
            definition = FlowRevision.migrate_definition(definition, self.flow, self.spec_version)
        return definition

    def as_json(self, include_definition=False):
        return dict(user=dict(email=self.created_by.username,
                    name=self.created_by.get_full_name()),
                    created_on=datetime_to_str(self.created_on),
                    id=self.pk,
                    version=self.spec_version,
                    revision=self.revision)


class FlowRunCount(models.Model):
    """
    Maintains counts of different states of exit types of flow runs on a flow. These are calculated
    via triggers on the database.
    """
    flow = models.ForeignKey(Flow, related_name='counts')
    exit_type = models.CharField(null=True, max_length=1, choices=FlowRun.EXIT_TYPE_CHOICES)
    count = models.IntegerField(default=0)

    LAST_SQUASH_KEY = 'last_flowruncount_squash'

    @classmethod
    def squash_counts(cls):
        # get the id of the last count we squashed
        r = get_redis_connection()
        last_squash = r.get(FlowRunCount.LAST_SQUASH_KEY)
        if not last_squash:
            last_squash = 0

        # get the unique flow ids for all new ones
        start = time.time()
        squash_count = 0
        for count in FlowRunCount.objects.filter(id__gt=last_squash).order_by('flow_id', 'exit_type').distinct('flow_id', 'exit_type'):
            print "Squashing: %d %s" % (count.flow_id, count.exit_type)

            # perform our atomic squash in SQL by calling our squash method
            with connection.cursor() as c:
                c.execute("SELECT temba_squash_flowruncount(%s, %s);", (count.flow_id, count.exit_type))

            squash_count += 1

        # insert our new top squashed id
        max_id = FlowRunCount.objects.all().order_by('-id').first()
        if max_id:
            r.set(FlowRunCount.LAST_SQUASH_KEY, max_id.id)

        print "Squashed run counts for %d pairs in %0.3fs" % (squash_count, time.time() - start)

    @classmethod
    def run_count(cls, flow):
        count = FlowRunCount.objects.filter(flow=flow)
        count = count.aggregate(Sum('count')).get('count__sum', 0)
        return 0 if count is None else count

    @classmethod
    def run_count_for_type(cls, flow, exit_type=None):
        count = FlowRunCount.objects.filter(flow=flow).filter(exit_type=exit_type)
        count = count.aggregate(Sum('count')).get('count__sum', 0)
        return 0 if count is None else count

    @classmethod
    def populate_for_flow(cls, flow):
        # remove old ones
        FlowRunCount.objects.filter(flow=flow).delete()

        # get test contacts on this org
        test_contacts = Contact.objects.filter(org=flow.org, is_test=True).values('id')

        # calculate our count for each exit type
        counts = FlowRun.objects.filter(flow=flow).exclude(contact__in=test_contacts)\
                                .values('exit_type').annotate(count=Count('pk'))

        # insert updated counts for each
        for count in counts:
            if count['count'] > 0:
                FlowRunCount.objects.create(flow=flow, exit_type=count['exit_type'], count=count['count'])

    def __unicode__(self):
        return "RunCount[%d:%s:%d]" % (self.flow_id, self.exit_type, self.count)

    class Meta:
        index_together = ('flow', 'exit_type')


class ExportFlowResultsTask(SmartModel):
    """
    Container for managing our export requests
    """
    INCLUDE_RUNS = 'include_runs'
    INCLUDE_MSGS = 'include_msgs'
    CONTACT_FIELDS = 'contact_fields'
    RESPONDED_ONLY = 'responded_only'

    org = models.ForeignKey(Org, related_name='flow_results_exports', help_text=_("The Organization of the user."))

    flows = models.ManyToManyField(Flow, related_name='exports', help_text=_("The flows to export"))

    task_id = models.CharField(null=True, max_length=64)

    is_finished = models.BooleanField(default=False, help_text=_("Whether this export is complete"))

    uuid = models.CharField(max_length=36, null=True,
                            help_text=_("The uuid used to name the resulting export file"))

    config = models.TextField(null=True,
                              help_text=_("Any configuration options for this flow export"))

    @classmethod
    def create(cls, org, user, flows, contact_fields, responded_only, include_runs, include_msgs):
        config = {ExportFlowResultsTask.INCLUDE_RUNS: include_runs,
                  ExportFlowResultsTask.INCLUDE_MSGS: include_msgs,
                  ExportFlowResultsTask.CONTACT_FIELDS: [c.id for c in contact_fields],
                  ExportFlowResultsTask.RESPONDED_ONLY: responded_only}

        export = ExportFlowResultsTask.objects.create(org=org, created_by=user, modified_by=user,
                                                      config=json.dumps(config))
        for flow in flows:
            export.flows.add(flow)

        return export

    def start_export(self):
        """
        Starts our export, wrapping it in a try block to make sure we mark it as finished when complete.
        """
        try:
            start = time.time()
            self.do_export()
        finally:
            elapsed = time.time() - start
            analytics.track(self.created_by.username, 'temba.flowresult_export_latency', properties=dict(value=elapsed))

            self.is_finished = True
            self.save(update_fields=['is_finished'])

    def do_export(self):
        from xlwt import Workbook
        book = Workbook()
        max_rows = 65535

        config = json.loads(self.config) if self.config else dict()
        include_runs = config.get(ExportFlowResultsTask.INCLUDE_RUNS, False)
        include_msgs = config.get(ExportFlowResultsTask.INCLUDE_MSGS, False)
        responded_only = config.get(ExportFlowResultsTask.RESPONDED_ONLY, True)
        contact_field_ids = config.get(ExportFlowResultsTask.CONTACT_FIELDS, [])

        contact_fields = []
        for cf_id in contact_field_ids:
            cf = ContactField.objects.filter(id=cf_id, org=self.org, is_active=True).first()
            if cf:
                contact_fields.append(cf)

        date_format = xlwt.easyxf(num_format_str='MM/DD/YYYY HH:MM:SS')
        small_width = 15 * 256
        medium_width = 20 * 256
        large_width = 100 * 256

        # merge the columns for all of our flows
        show_submitted_by = False
        columns = []
        flows = self.flows.all()
        with SegmentProfiler("get columns"):
            for flow in flows:
                columns += flow.get_columns()

                if flow.flow_type == Flow.SURVEY:
                    show_submitted_by = True

        org = None
        if flows:
            org = flows[0].org

        org_tz = pytz.timezone(flows[0].org.timezone)

        def as_org_tz(dt):
            if dt:
                return dt.astimezone(org_tz).replace(tzinfo=None)
            else:
                return None

        # create a mapping of column id to index
        column_map = dict()
        for col in range(len(columns)):
            column_map[columns[col].uuid] = 6 + len(contact_fields) + col * 3

        # build a cache of rule uuid to category name, we want to use the most recent name the user set
        # if possible and back down to the cached rule_category only when necessary
        category_map = dict()

        with SegmentProfiler("rule uuid to category to name"):
            for ruleset in RuleSet.objects.filter(flow__in=flows).select_related('flow'):
                for rule in ruleset.get_rules():
                    category_map[rule.uuid] = rule.get_category_name(ruleset.flow.base_language)

        ruleset_steps = FlowStep.objects.filter(run__flow__in=flows, step_type=FlowStep.TYPE_RULE_SET)
        ruleset_steps = ruleset_steps.order_by('contact', 'run', 'arrived_on', 'pk')

        if responded_only:
            ruleset_steps = ruleset_steps.filter(run__responded=True)

        # count of unique flow runs
        with SegmentProfiler("# of runs"):
            all_runs_count = ruleset_steps.values('run').distinct().count()

        # count of unique contacts
        with SegmentProfiler("# of contacts"):
            contacts_count = ruleset_steps.values('contact').distinct().count()

        # grab the ids for all our steps so we don't have to ever calculate them again
        with SegmentProfiler("calculate step ids"):
            all_steps = FlowStep.objects.filter(run__flow__in=flows)\
                                        .order_by('contact', 'run', 'arrived_on', 'pk')\
                                        .values('id')

            if responded_only:
                all_steps = all_steps.filter(run__responded=True)

            step_ids = [s['id'] for s in all_steps]

        # build our sheets
        run_sheets = []
        total_run_sheet_count = 0

        # the full sheets we need for runs
        if include_runs:
            for i in range(all_runs_count / max_rows + 1):
                total_run_sheet_count += 1
                name = "Runs" if (i + 1) <= 1 else "Runs (%d)" % (i + 1)
                sheet = book.add_sheet(name, cell_overwrite_ok=True)
                run_sheets.append(sheet)

        total_merged_run_sheet_count = 0

        # the full sheets we need for contacts
        for i in range(contacts_count / max_rows + 1):
            total_merged_run_sheet_count += 1
            name = "Contacts" if (i + 1) <= 1 else "Contacts (%d)" % (i + 1)
            sheet = book.add_sheet(name, cell_overwrite_ok=True)
            run_sheets.append(sheet)

        # then populate their header columns
        for (sheet_num, sheet) in enumerate(run_sheets):
            # build up our header row

            index = 0
            if show_submitted_by:
                sheet.write(0, index, "Surveyor")
                sheet.col(0).width = medium_width
                index += 1

            sheet.write(0, index, "Contact UUID")
            sheet.col(index).width = medium_width
            index += 1

            sheet.write(0, index, "URN")
            sheet.col(index).width = small_width
            index += 1

            sheet.write(0, index, "Name")
            sheet.col(index).width = medium_width
            index += 1

            sheet.write(0, index, "Groups")
            sheet.col(index).width = medium_width
            index += 1

            # add our contact fields
            for cf in contact_fields:
                sheet.write(0, index, cf.label)
                sheet.col(index).width = medium_width
                index += 1

            sheet.write(0, index, "First Seen")
            sheet.col(index).width = medium_width
            index += 1

            sheet.write(0, index, "Last Seen")
            sheet.col(index).width = medium_width
            index += 1

            for col in range(len(columns)):
                ruleset = columns[col]
                sheet.write(0, index + col * 3, "%s (Category) - %s" % (unicode(ruleset.label), unicode(ruleset.flow.name)))
                sheet.write(0, index + col * 3 + 1, "%s (Value) - %s" % (unicode(ruleset.label), unicode(ruleset.flow.name)))
                sheet.write(0, index + col * 3 + 2, "%s (Text) - %s" % (unicode(ruleset.label), unicode(ruleset.flow.name)))
                sheet.col(index + col * 3).width = 15 * 256
                sheet.col(index + col * 3 + 1).width = 15 * 256
                sheet.col(index + col * 3 + 2).width = 15 * 256

        run_row = 0
        merged_row = 0
        msg_row = 0

        latest = None
        earliest = None
        merged_latest = None
        merged_earliest = None

        last_run = 0
        last_contact = None

        # index of sheets that we are currently writing to
        run_sheet_index = 0
        merged_run_sheet_index = total_run_sheet_count
        msg_sheet_index = 0

        # get our initial runs and merged runs to write to
        runs = book.get_sheet(run_sheet_index)
        merged_runs = book.get_sheet(merged_run_sheet_index)
        msgs = None

        processed_steps = 0
        total_steps = len(step_ids)
        start = time.time()
        flow_names = ", ".join([f['name'] for f in self.flows.values('name')])

        urn_display_cache = {}

        def get_contact_urn_display(contact):
            """
            Gets the possibly cached URN display (e.g. formatted phone number) for the given contact
            """
            urn_display = urn_display_cache.get(contact.pk)
            if urn_display:
                return urn_display
            urn_display = contact.get_urn_display(org=org, full=True)
            urn_display_cache[contact.pk] = urn_display
            return urn_display

        for run_step in ChunkIterator(FlowStep, step_ids,
                                      order_by=['contact', 'run', 'arrived_on', 'pk'],
                                      select_related=['run', 'contact'],
                                      prefetch_related=['messages__contact_urn',
                                                        'messages__channel',
                                                        'broadcasts',
                                                        'contact__all_groups'],
                                      contact_fields=contact_fields):

            processed_steps += 1
            if processed_steps % 10000 == 0:
                print "Export of %s - %d%% complete in %0.2fs" % \
                      (flow_names, processed_steps * 100 / total_steps, time.time() - start)

            # skip over test contacts
            if run_step.contact.is_test:
                continue

            contact_urn_display = get_contact_urn_display(run_step.contact)
            contact_uuid = run_step.contact.uuid

            # if this is a rule step, write out the value collected
            if run_step.step_type == FlowStep.TYPE_RULE_SET:

                # a new contact
                if last_contact != run_step.contact.pk:
                    merged_earliest = run_step.arrived_on
                    merged_latest = None

                    if merged_row % 1000 == 0:
                        merged_runs.flush_row_data()

                    merged_row += 1

                    if merged_row > max_rows:
                        # get the next sheet to use for Contacts
                        merged_row = 1
                        merged_run_sheet_index += 1
                        merged_runs = book.get_sheet(merged_run_sheet_index)

                # a new run
                if last_run != run_step.run.pk:
                    earliest = run_step.arrived_on
                    latest = None

                    if include_runs:
                        if run_row % 1000 == 0:
                            runs.flush_row_data()

                        run_row += 1

                        if run_row > max_rows:
                            # get the next sheet to use for Runs
                            run_row = 1
                            run_sheet_index += 1
                            runs = book.get_sheet(run_sheet_index)

                    # build up our group names
                    group_names = []
                    for group in run_step.contact.all_groups.all():
                        if group.group_type == ContactGroup.TYPE_USER_DEFINED:
                            group_names.append(group.name)

                    group_names.sort()
                    groups = ", ".join(group_names)

                    padding = 0
                    if show_submitted_by:
                        submitted_by = ''

                        # use the login as the submission user
                        if run_step.run.submitted_by:
                            submitted_by = run_step.run.submitted_by.username

                        if include_runs:
                            runs.write(run_row, 0, submitted_by)
                        merged_runs.write(merged_row, 0, submitted_by)
                        padding = 1

                    if include_runs:
                        runs.write(run_row, padding + 0, contact_uuid)
                        runs.write(run_row, padding + 1, contact_urn_display)
                        runs.write(run_row, padding + 2, run_step.contact.name)
                        runs.write(run_row, padding + 3, groups)

                    merged_runs.write(merged_row, padding + 0, contact_uuid)
                    merged_runs.write(merged_row, padding + 1, contact_urn_display)
                    merged_runs.write(merged_row, padding + 2, run_step.contact.name)
                    merged_runs.write(merged_row, padding + 3, groups)

                    cf_padding = 0

                    # write our contact fields if any
                    for cf in contact_fields:
                        field_value = Contact.get_field_display_for_value(cf, run_step.contact.get_field(cf.key.lower()))
                        if field_value is None:
                            field_value = ''

                        field_value = unicode(field_value)

                        merged_runs.write(merged_row, padding + 4 + cf_padding, field_value)
                        if include_runs:
                            runs.write(run_row, padding + 4 + cf_padding, field_value)

                        cf_padding += 1

                if not latest or latest < run_step.arrived_on:
                    latest = run_step.arrived_on

                if not merged_latest or merged_latest < run_step.arrived_on:
                    merged_latest = run_step.arrived_on

                if include_runs:
                    runs.write(run_row, padding + 4 + cf_padding, as_org_tz(earliest), date_format)
                    runs.write(run_row, padding + 5 + cf_padding, as_org_tz(latest), date_format)

                merged_runs.write(merged_row, padding + 4 + cf_padding, as_org_tz(merged_earliest), date_format)
                merged_runs.write(merged_row, padding + 5 + cf_padding, as_org_tz(merged_latest), date_format)

                # write the step data
                col = column_map.get(run_step.step_uuid, 0) + padding
                if col:
                    category = category_map.get(run_step.rule_uuid, None)
                    if category:
                        if include_runs:
                            runs.write(run_row, col, category)
                        merged_runs.write(merged_row, col, category)
                    elif run_step.rule_category:
                        if include_runs:
                            runs.write(run_row, col, run_step.rule_category)
                        merged_runs.write(merged_row, col, run_step.rule_category)

                    value = run_step.rule_value
                    if value:
                        if include_runs:
                            runs.write(run_row, col + 1, value)
                        merged_runs.write(merged_row, col + 1, value)

                    text = run_step.get_text()
                    if text:
                        if include_runs:
                            runs.write(run_row, col + 2, text)
                        merged_runs.write(merged_row, col + 2, text)

                last_run = run_step.run.pk
                last_contact = run_step.contact.pk

            # write out any message associated with this step
            if include_msgs:
                step_msgs = list(run_step.messages.all())

                if step_msgs:
                    msg = step_msgs[0]
                    msg_row += 1

                    if msg_row % 1000 == 0:
                        msgs.flush_row_data()

                    if msg_row > max_rows or not msgs:
                        msg_row = 1
                        msg_sheet_index += 1

                        name = "Messages" if (msg_sheet_index + 1) <= 1 else "Messages (%d)" % (msg_sheet_index + 1)
                        msgs = book.add_sheet(name)

                        msgs.write(0, 0, "Contact UUID")
                        msgs.write(0, 1, "URN")
                        msgs.write(0, 2, "Name")
                        msgs.write(0, 3, "Date")
                        msgs.write(0, 4, "Direction")
                        msgs.write(0, 5, "Message")
                        msgs.write(0, 6, "Channel")

                        msgs.col(0).width = medium_width
                        msgs.col(1).width = small_width
                        msgs.col(2).width = medium_width
                        msgs.col(3).width = medium_width
                        msgs.col(4).width = small_width
                        msgs.col(5).width = large_width
                        msgs.col(6).width = small_width

                    msg_urn_display = msg.contact_urn.get_display(org=org, full=True) if msg.contact_urn else ''
                    channel_name = msg.channel.name if msg.channel else ''

                    msgs.write(msg_row, 0, run_step.contact.uuid)
                    msgs.write(msg_row, 1, msg_urn_display)
                    msgs.write(msg_row, 2, run_step.contact.name)
                    msgs.write(msg_row, 3, as_org_tz(msg.created_on), date_format)
                    msgs.write(msg_row, 4, "IN" if msg.direction == INCOMING else "OUT")
                    msgs.write(msg_row, 5, msg.text)
                    msgs.write(msg_row, 6, channel_name)

        temp = NamedTemporaryFile(delete=True)
        book.save(temp)
        temp.flush()

        # initialize the UUID which we will save results as
        self.uuid = str(uuid4())
        self.save(update_fields=['uuid'])

        # save as file asset associated with this task
        from temba.assets.models import AssetType
        from temba.assets.views import get_asset_url

        store = AssetType.results_export.store
        store.save(self.pk, File(temp), 'xls')

        subject = "Your export is ready"
        template = 'flows/email/flow_export_download'

        branding = self.org.get_branding()
        download_url = branding['link'] + get_asset_url(AssetType.results_export, self.pk)

        # force a gc
        import gc
        gc.collect()

        # only send the email if this is production
        send_template_email(self.created_by.username, subject, template, dict(flows=flows, link=download_url), branding)


class ActionLog(models.Model):
    """
    Log of an event that occurred whilst executing a flow in the simulator
    """
    LEVEL_INFO = 'I'
    LEVEL_WARN = 'W'
    LEVEL_ERROR = 'E'
    LEVEL_CHOICES = ((LEVEL_INFO, _("Info")), (LEVEL_WARN, _("Warning")), (LEVEL_ERROR, _("Error")))

    run = models.ForeignKey(FlowRun, related_name='logs')

    text = models.TextField(help_text=_("Log event text"))

    level = models.CharField(max_length=1, choices=LEVEL_CHOICES, default=LEVEL_INFO, help_text=_("Log event level"))

    created_on = models.DateTimeField(auto_now_add=True, help_text=_("When this log event occurred"))

    @classmethod
    def create(cls, run, text, level=LEVEL_INFO, safe=False):
        if not safe:
            text = escape(text)

        text = text.replace('\n', "<br/>")

        try:
            return ActionLog.objects.create(run=run, text=text, level=level)
        except Exception:  # pragma: no cover
            return None  # it's possible our test run can be deleted out from under us

    @classmethod
    def info(cls, run, text, safe=False):
        return cls.create(run, text, cls.LEVEL_INFO, safe)

    @classmethod
    def warn(cls, run, text, safe=False):
        return cls.create(run, text, cls.LEVEL_WARN, safe)

    @classmethod
    def error(cls, run, text, safe=False):
        return cls.create(run, text, cls.LEVEL_ERROR, safe)

    def as_json(self):
        return dict(id=self.id,
                    direction="O",
                    level=self.level,
                    text=self.text,
                    created_on=self.created_on.strftime('%x %X'),
                    model="log")

    def simulator_json(self):
        return self.as_json()

    def __unicode__(self):
        return self.text


class FlowStart(SmartModel):
    STATUS_PENDING = 'P'
    STATUS_STARTING = 'S'
    STATUS_COMPLETE = 'C'
    STATUS_FAILED = 'F'

    STATUS_CHOICES = ((STATUS_PENDING, "Pending"),
                      (STATUS_STARTING, "Starting"),
                      (STATUS_COMPLETE, "Complete"),
                      (STATUS_FAILED, "Failed"))

    flow = models.ForeignKey(Flow, related_name='starts', help_text=_("The flow that is being started"))

    groups = models.ManyToManyField(ContactGroup, help_text=_("Groups that will start the flow"))

    contacts = models.ManyToManyField(Contact, help_text=_("Contacts that will start the flow"))

    restart_participants = models.BooleanField(default=True,
                                               help_text=_("Whether to restart any participants already in this flow"))

    contact_count = models.IntegerField(default=0,
                                        help_text=_("How many unique contacts were started down the flow"))

    status = models.CharField(max_length=1, default=STATUS_PENDING, choices=STATUS_CHOICES,
                              help_text=_("The status of this flow start"))

    extra = models.TextField(null=True,
                             help_text=_("Any extra parameters to pass to the flow start (json)"))

    @classmethod
    def create(cls, flow, user, groups=None, contacts=None, restart_participants=True, extra=None):
        if contacts is None:
            contacts = []

        if groups is None:
            groups = []

        start = FlowStart.objects.create(flow=flow, restart_participants=restart_participants,
                                         extra=json.dumps(extra) if extra else None,
                                         created_by=user, modified_by=user)

        for contact in contacts:
            start.contacts.add(contact)

        for group in groups:
            start.groups.add(group)

        return start

    def async_start(self):
        from temba.flows.tasks import start_flow_task
        start_flow_task.delay(self.id)

    def start(self):
        self.status = FlowStart.STATUS_STARTING
        self.save(update_fields=['status'])

        try:
            groups = [g for g in self.groups.all()]
            contacts = [c for c in self.contacts.all().only('is_test')]

            # load up our extra if any
            extra = json.loads(self.extra) if self.extra else None

            self.flow.start(groups, contacts, restart_participants=self.restart_participants, flow_start=self, extra=extra)

        except Exception as e:  # pragma: no cover
            import traceback
            traceback.print_exc(e)

            self.status = FlowStart.STATUS_FAILED
            self.save(update_fields=['status'])
            raise e

    def update_status(self):
        # only update our status to complete if we have started as many runs as our total contact count
        if self.runs.count() == self.contact_count:
            self.status = FlowStart.STATUS_COMPLETE
            self.save(update_fields=['status'])

    def __unicode__(self):  # pragma: no cover
        return "FlowStart %d (Flow %d)" % (self.id, self.flow_id)


class FlowLabel(models.Model):
    org = models.ForeignKey(Org)

    uuid = models.CharField(max_length=36, unique=True, db_index=True, default=generate_uuid,
                            verbose_name=_("Unique Identifier"), help_text=_("The unique identifier for this label"))
    name = models.CharField(max_length=64, verbose_name=_("Name"),
                            help_text=_("The name of this flow label"))
    parent = models.ForeignKey('FlowLabel', verbose_name=_("Parent"), null=True, related_name="children")

    def get_flows_count(self):
        """
        Returns the count of flows tagged with this label or one of its children
        """
        return self.get_flows().count()

    def get_flows(self):
        return Flow.objects.filter(Q(labels=self) | Q(labels__parent=self)).filter(is_active=True, is_archived=False).distinct()

    @classmethod
    def create_unique(cls, base, org, parent=None):

        base = base.strip()

        # truncate if necessary
        if len(base) > 32:
            base = base[:32]

        # find the next available label by appending numbers
        count = 2
        while FlowLabel.objects.filter(name=base, org=org, parent=parent):
            # make room for the number
            if len(base) >= 32:
                base = base[:30]
            last = str(count - 1)
            if base.endswith(last):
                base = base[:-len(last)]
            base = "%s %d" % (base.strip(), count)
            count += 1

        return FlowLabel.objects.create(name=base, org=org, parent=parent)

    def toggle_label(self, flows, add):
        changed = []

        for flow in flows:
            # if we are adding the flow label and this flow doesnt have it, add it
            if add:
                if not flow.labels.filter(pk=self.pk):
                    flow.labels.add(self)
                    changed.append(flow.pk)

            # otherwise, remove it if not already present
            else:
                if flow.labels.filter(pk=self.pk):
                    flow.labels.remove(self)
                    changed.append(flow.pk)

        return changed

    def __unicode__(self):
        if self.parent:
            return "%s > %s" % (self.parent, self.name)
        return self.name

    class Meta:
        unique_together = ('name', 'parent', 'org')


__flow_user = None


def get_flow_user():
    global __flow_user
    if not __flow_user:
        user = User.objects.filter(username='flow').first()
        if user:
            __flow_user = user
        else:
            user = User.objects.create_user('flow')
            user.groups.add(Group.objects.get(name='Service Users'))
            __flow_user = user

    return __flow_user


class Action(object):
    """
    Base class for actions that can be added to an action set and executed during a flow run
    """
    TYPE = 'type'
    __action_mapping = None

    @classmethod
    def from_json(cls, org, json_obj):
        if not cls.__action_mapping:
            cls.__action_mapping = {
                ReplyAction.TYPE: ReplyAction,
                SendAction.TYPE: SendAction,
                AddToGroupAction.TYPE: AddToGroupAction,
                DeleteFromGroupAction.TYPE: DeleteFromGroupAction,
                AddLabelAction.TYPE: AddLabelAction,
                EmailAction.TYPE: EmailAction,
                WebhookAction.TYPE: WebhookAction,
                SaveToContactAction.TYPE: SaveToContactAction,
                SetLanguageAction.TYPE: SetLanguageAction,
                SetChannelAction.TYPE: SetChannelAction,
                StartFlowAction.TYPE: StartFlowAction,
                SayAction.TYPE: SayAction,
                PlayAction.TYPE: PlayAction,
                TriggerFlowAction.TYPE: TriggerFlowAction,
            }

        action_type = json_obj.get(cls.TYPE)
        if not action_type:  # pragma: no cover
            raise FlowException("Action definition missing 'type' attribute: %s" % json_obj)

        if action_type not in cls.__action_mapping:  # pragma: no cover
            raise FlowException("Unknown action type '%s' in definition: '%s'" % (action_type, json_obj))

        return cls.__action_mapping[action_type].from_json(org, json_obj)

    @classmethod
    def from_json_array(cls, org, json_arr):
        actions = []
        for inner in json_arr:
            action = Action.from_json(org, inner)
            if action:
                actions.append(action)
        return actions


class EmailAction(Action):
    """
    Sends an email to someone
    """
    TYPE = 'email'
    EMAILS = 'emails'
    SUBJECT = 'subject'
    MESSAGE = 'msg'

    def __init__(self, emails, subject, message):
        if not emails:
            raise FlowException("Email actions require at least one recipient")

        self.emails = emails
        self.subject = subject
        self.message = message

    @classmethod
    def from_json(cls, org, json_obj):
        emails = json_obj.get(EmailAction.EMAILS)
        message = json_obj.get(EmailAction.MESSAGE)
        subject = json_obj.get(EmailAction.SUBJECT)
        return EmailAction(emails, subject, message)

    def as_json(self):
        return dict(type=EmailAction.TYPE, emails=self.emails, subject=self.subject, msg=self.message)

    def execute(self, run, actionset_uuid, msg, offline_on=None):
        from .tasks import send_email_action_task

        # build our message from our flow variables
        message_context = run.flow.build_message_context(run.contact, msg)
        (message, errors) = Msg.substitute_variables(self.message, run.contact, message_context, org=run.flow.org)
        (subject, errors) = Msg.substitute_variables(self.subject, run.contact, message_context, org=run.flow.org)

        # make sure the subject is single line; replace '\t\n\r\f\v' to ' '
        subject = regex.sub('\s+', ' ', subject, regex.V0)

        valid_addresses = []
        invalid_addresses = []
        for email in self.emails:
            if email[0] == '@':
                # a valid email will contain @ so this is very likely to generate evaluation errors
                (address, errors) = Msg.substitute_variables(email, run.contact, message_context, org=run.flow.org)
            else:
                address = email

            address = address.strip()

            if is_valid_address(address):
                valid_addresses.append(address)
            else:
                invalid_addresses.append(address)

        if not run.contact.is_test:
            if valid_addresses:
                send_email_action_task.delay(valid_addresses, subject, message)
        else:
            if valid_addresses:
                ActionLog.info(run, _("\"%s\" would be sent to %s") % (message, ", ".join(valid_addresses)))
            if invalid_addresses:
                ActionLog.warn(run, _("Some email address appear to be invalid: %s") % ", ".join(invalid_addresses))
        return []


class WebhookAction(Action):
    """
    Forwards the steps in this flow to the webhook (if any)
    """
    TYPE = 'api'
    ACTION = 'action'

    def __init__(self, webhook, action='POST'):
        self.webhook = webhook
        self.action = action

    @classmethod
    def from_json(cls, org, json_obj):
        return WebhookAction(json_obj.get('webhook', org.get_webhook_url()), json_obj.get('action', 'POST'))

    def as_json(self):
        return dict(type=WebhookAction.TYPE, webhook=self.webhook, action=self.action)

    def execute(self, run, actionset_uuid, msg, offline_on=None):
        from temba.api.models import WebHookEvent

        message_context = run.flow.build_message_context(run.contact, msg)
        (value, errors) = Msg.substitute_variables(self.webhook, run.contact, message_context,
                                                   org=run.flow.org, url_encode=True)

        if errors:
            ActionLog.warn(run, _("URL appears to contain errors: %s") % ", ".join(errors))

        WebHookEvent.trigger_flow_event(value, run.flow, run, actionset_uuid, run.contact, msg, self.action)
        return []


class AddToGroupAction(Action):
    """
    Adds the user to a group
    """
    TYPE = 'add_group'
    GROUP = 'group'
    GROUPS = 'groups'
    UUID = 'uuid'
    NAME = 'name'

    def __init__(self, groups):
        self.groups = groups

    @classmethod
    def from_json(cls, org, json_obj):
        return AddToGroupAction(AddToGroupAction.get_groups(org, json_obj))

    @classmethod
    def get_groups(cls, org, json_obj):

        # for backwards compatibility
        group_data = json_obj.get(AddToGroupAction.GROUP, None)
        if not group_data:
            group_data = json_obj.get(AddToGroupAction.GROUPS)
        else:
            group_data = [group_data]

        groups = []

        for g in group_data:
            if isinstance(g, dict):
                group_uuid = g.get(AddToGroupAction.UUID, None)
                group_name = g.get(AddToGroupAction.NAME)

                group = ContactGroup.get_or_create(org, org.created_by, group_name, group_uuid)
                groups.append(group)
            else:
                if g and g[0] == '@':
                    groups.append(g)
                else:
                    group = ContactGroup.get_user_group(org, g)
                    if group:
                        groups.append(group)
                    else:
                        groups.append(ContactGroup.create_static(org, org.get_user(), g))
        return groups

    def as_json(self):
        groups = []
        for g in self.groups:
            if isinstance(g, ContactGroup):
                groups.append(dict(uuid=g.uuid, name=g.name))
            else:
                groups.append(g)

        return dict(type=self.get_type(), groups=groups)

    def get_type(self):
        return AddToGroupAction.TYPE

    def execute(self, run, actionset_uuid, msg, offline_on=None):
        contact = run.contact
        add = AddToGroupAction.TYPE == self.get_type()
        user = get_flow_user()

        if contact:
            for group in self.groups:
                if not isinstance(group, ContactGroup):

                    message_context = run.flow.build_message_context(contact, msg)
                    (value, errors) = Msg.substitute_variables(group, contact, message_context, org=run.flow.org)
                    group = None

                    if not errors:
                        group = ContactGroup.get_user_group(contact.org, value)
                        if not group:

                            try:
                                group = ContactGroup.create_static(contact.org, user, name=value)
                                if run.contact.is_test:
                                    ActionLog.info(run, _("Group '%s' created") % value)
                            except ValueError:
                                    ActionLog.error(run, _("Unable to create group with name '%s'") % value)
                    else:
                        ActionLog.error(run, _("Group name could not be evaluated: %s") % ', '.join(errors))

                if group:
                    # TODO should become a failure (because it should be impossible) and not just a simulator error
                    if group.is_dynamic:
                        # report to sentry
                        logger.error("Attempt to add/remove contacts on dynamic group '%s' [%d] "
                                     "in flow '%s' [%d] for org '%s' [%d]"
                                     % (group.name, group.pk, run.flow.name, run.flow.pk, run.org.name, run.org.pk))
                        if run.contact.is_test:
                            if add:
                                ActionLog.error(run, _("%s is a dynamic group which we can't add contacts to") % group.name)
                            else:
                                ActionLog.error(run, _("%s is a dynamic group which we can't remove contacts from") % group.name)
                        continue

                    group.update_contacts(user, [contact], add)
                    if run.contact.is_test:
                        if add:
                            ActionLog.info(run, _("Added %s to %s") % (run.contact.name, group.name))
                        else:
                            ActionLog.info(run, _("Removed %s from %s") % (run.contact.name, group.name))
        return []


class DeleteFromGroupAction(AddToGroupAction):
    """
    Removes the user from a group
    """
    TYPE = 'del_group'

    def get_type(self):
        return DeleteFromGroupAction.TYPE

    def as_json(self):
        groups = []
        for g in self.groups:
            if isinstance(g, ContactGroup):
                groups.append(dict(uuid=g.uuid, name=g.name))
            else:
                groups.append(g)

        return dict(type=self.get_type(), groups=groups)

    @classmethod
    def from_json(cls, org, json_obj):
        return DeleteFromGroupAction(DeleteFromGroupAction.get_groups(org, json_obj))

    def execute(self, run, actionset, sms):
        if len(self.groups) == 0:
            contact = run.contact
            user = get_flow_user()
            if contact:
                # remove from all active and inactive user-defined, static groups
                for group in ContactGroup.user_groups.filter(org=contact.org,
                                                             group_type=ContactGroup.TYPE_USER_DEFINED,
                                                             query__isnull=True):
                    group.update_contacts(user, [contact], False)
                    if run.contact.is_test:
                        ActionLog.info(run, _("Removed %s from %s") % (run.contact.name, group.name))
            return []
        return AddToGroupAction.execute(self, run, actionset, sms)


class AddLabelAction(Action):
    """
    Add a label to the incoming message
    """
    TYPE = 'add_label'
    LABELS = 'labels'
    UUID = 'uuid'
    NAME = 'name'

    def __init__(self, labels):
        self.labels = labels

    @classmethod
    def from_json(cls, org, json_obj):
        labels_data = json_obj.get(AddLabelAction.LABELS)

        labels = []
        for label_data in labels_data:
            if isinstance(label_data, dict):
                label_uuid = label_data.get(AddLabelAction.UUID, None)
                label_name = label_data.get(AddLabelAction.NAME)

                if label_uuid and Label.label_objects.filter(org=org, uuid=label_uuid).first():
                    label = Label.label_objects.filter(org=org, uuid=label_uuid).first()
                    if label:
                        labels.append(label)
                else:
                    labels.append(Label.get_or_create(org, org.get_user(), label_name))

            elif isinstance(label_data, basestring):
                if label_data and label_data[0] == '@':
                    # label name is a variable substitution
                    labels.append(label_data)
                else:
                    labels.append(Label.get_or_create(org, org.get_user(), label_data))
            else:
                raise ValueError("Label data must be a dict or string")

        return AddLabelAction(labels)

    def as_json(self):
        labels = []
        for action_label in self.labels:
            if isinstance(action_label, Label):
                labels.append(dict(uuid=action_label.uuid, name=action_label.name))
            else:
                labels.append(action_label)

        return dict(type=self.get_type(), labels=labels)

    def get_type(self):
        return AddLabelAction.TYPE

    def execute(self, run, actionset_uuid, msg, offline_on=None):
        for label in self.labels:
            if not isinstance(label, Label):
                contact = run.contact
                message_context = run.flow.build_message_context(contact, msg)
                (value, errors) = Msg.substitute_variables(label, contact, message_context, org=run.flow.org)

                if not errors:
                    try:
                        label = Label.get_or_create(contact.org, contact.org.get_user(), value)
                        if run.contact.is_test:
                            ActionLog.info(run, _("Label '%s' created") % label.name)
                    except ValueError:
                        ActionLog.error(run, _("Unable to create label with name '%s'") % label.name)
                else:
                    label = None
                    ActionLog.error(run, _("Label name could not be evaluated: %s") % ', '.join(errors))

            if label and msg and msg.pk:
                if run.contact.is_test:
                    # don't really add labels to simulator messages
                    ActionLog.info(run, _("Added %s label to msg '%s'") % (label.name, msg.text))
                else:
                    label.toggle_label([msg], True)
        return []


class SayAction(Action):
    """
    Voice action for reading some text to a user
    """
    TYPE = 'say'
    MESSAGE = 'msg'
    UUID = 'uuid'
    RECORDING = 'recording'

    def __init__(self, uuid, msg, recording):
        self.uuid = uuid
        self.msg = msg
        self.recording = recording

    @classmethod
    def from_json(cls, org, json_obj):
        return SayAction(json_obj.get(SayAction.UUID),
                         json_obj.get(SayAction.MESSAGE),
                         json_obj.get(SayAction.RECORDING))

    def as_json(self):
        return dict(type=SayAction.TYPE, msg=self.msg,
                    uuid=self.uuid, recording=self.recording)

    def execute(self, run, actionset_uuid, event, offline_on=None):

        media_url = None
        if self.recording:

            # localize our recording
            recording = run.flow.get_localized_text(self.recording, run.contact)

            # if we have a localized recording, create the url
            if recording:
                media_url = "https://%s/%s" % (settings.AWS_BUCKET_DOMAIN, recording)

        # localize the text for our message, need this either way for logging
        message = run.flow.get_localized_text(self.msg, run.contact)
        (message, errors) = Msg.substitute_variables(message, run.contact, run.flow.build_message_context(run.contact, event))

        msg = run.create_outgoing_ivr(message, media_url)

        if msg:
            if run.contact.is_test:
                if media_url:
                    ActionLog.create(run, _('Played recorded message for "%s"') % message)
                else:
                    ActionLog.create(run, _('Read message "%s"') % message)
            return [msg]
        else:
            # no message, possibly failed loop detection
            run.voice_response.say(_("Sorry, an invalid flow has been detected. Good bye."))
            return []


class PlayAction(Action):
    """
    Voice action for reading some text to a user
    """
    TYPE = 'play'
    URL = 'url'
    UUID = 'uuid'

    def __init__(self, uuid, url):
        self.uuid = uuid
        self.url = url

    @classmethod
    def from_json(cls, org, json_obj):
        return PlayAction(json_obj.get(PlayAction.UUID), json_obj.get(PlayAction.URL))

    def as_json(self):
        return dict(type=PlayAction.TYPE, url=self.url, uuid=self.uuid)

    def execute(self, run, actionset_uuid, event, offline_on=None):

        (media, errors) = Msg.substitute_variables(self.url, run.contact, run.flow.build_message_context(run.contact, event))
        msg = run.create_outgoing_ivr(_('Played contact recording'), media)

        if msg:
            if run.contact.is_test:
                log_txt = _('Played recording at "%s"') % msg.media
                ActionLog.create(run, log_txt)
            return [msg]
        else:
            # no message, possibly failed loop detection
            run.voice_response.say(_("Sorry, an invalid flow has been detected. Good bye."))
            return []


class ReplyAction(Action):
    """
    Simple action for sending back a message
    """
    TYPE = 'reply'
    MESSAGE = 'msg'

    def __init__(self, msg=None):
        self.msg = msg

    @classmethod
    def from_json(cls, org, json_obj):
        # assert we have some kind of message in this reply
        msg = json_obj.get(ReplyAction.MESSAGE)
        if isinstance(msg, dict):
            if not msg:
                raise FlowException("Invalid reply action, empty message dict")

            if not any([v for v in msg.values()]):
                raise FlowException("Invalid reply action, missing at least one message")
        elif not msg:
            raise FlowException("Invalid reply action, no message")

        return ReplyAction(msg=json_obj.get(ReplyAction.MESSAGE))

    def as_json(self):
        return dict(type=ReplyAction.TYPE, msg=self.msg)

    def execute(self, run, actionset_uuid, msg, offline_on=None):
        reply = None

        if self.msg:
            user = get_flow_user()
            text = run.flow.get_localized_text(self.msg, run.contact)

            if offline_on:
                reply = Msg.create_outgoing(run.org, user, (run.contact, None), text, status=SENT,
                                            created_on=offline_on, response_to=msg)
            else:
                context = run.flow.build_message_context(run.contact, msg)
                try:
                    if msg:
                        reply = msg.reply(text, user, trigger_send=False, message_context=context)
                    else:
                        reply = run.contact.send(text, user, trigger_send=False, message_context=context)
                except UnreachableException:
                    pass

        return [reply] if reply else []


class UssdAction(ReplyAction):
    """
    USSD action to send outgoing USSD messages
    Created from a USSD ruleset
    It builds localised text with localised USSD menu support
    """
    TYPE = 'ussd'
    MESSAGE = 'ussd_message'
    TYPE_WAIT_USSD_MENU = 'wait_menu'
    TYPE_WAIT_USSD = 'wait_ussd'

    def __init__(self, msg=None, base_language=None, languages=None, primary_language=None):
        super(UssdAction, self).__init__(msg)
        self.languages = languages
        if msg and base_language and primary_language:
            self.base_language = base_language if base_language in msg else primary_language
        else:
            self.base_language = None

    @classmethod
    def from_ruleset(cls, ruleset, run):
        if ruleset and hasattr(ruleset, 'config') and isinstance(ruleset.config, basestring):
            # initial message, menu obj
            obj = json.loads(ruleset.config)
            rules = json.loads(ruleset.rules)
            msg = obj.get(cls.MESSAGE, '')
            org = run.flow.org

            # define languages
            base_language = run.flow.base_language
            org_languages = {l.iso_code for l in org.languages.all()}
            primary_language = getattr(getattr(org, 'primary_language', None), 'iso_code', None)

            # initialize UssdAction
            ussd_action = cls(msg=msg, base_language=base_language, languages=org_languages,
                              primary_language=primary_language)

            ussd_action.substitute_missing_languages()

            if ruleset.ruleset_type == cls.TYPE_WAIT_USSD_MENU:
                ussd_action.add_menu_to_msg(rules)

            return ussd_action
        else:
            return cls()

    def substitute_missing_languages(self):
        # if there is a translation missing fill it with the base language
        for language in self.languages:
            if language not in self.msg:
                self.msg[language] = self.msg.get(self.base_language)

    def get_menu_label(self, label, language):
        if language not in label:
            return str(label.get(self.base_language))
        else:
            return str(label[language])

    def add_menu_to_msg(self, rules):
        # start with a new line
        self.msg = {language: localised_msg + '\n' for language, localised_msg in self.msg.iteritems()}

        # add menu to the msg
        for rule in rules:
            if rule.get('label'):  # filter "other" and "interrupted"
                self.msg = {language: localised_msg + ": ".join(
                    (str(rule['test']['test']), self.get_menu_label(rule['label'], language),)) + '\n' for language, localised_msg in self.msg.iteritems()}


class VariableContactAction(Action):
    """
    Base action that resolves variables into contacts. Used for actions that take
    SendAction, TriggerAction, etc
    """
    CONTACTS = 'contacts'
    GROUPS = 'groups'
    VARIABLES = 'variables'
    PHONE = 'phone'
    NAME = 'name'
    UUID = 'uuid'
    ID = 'id'

    def __init__(self, groups, contacts, variables):
        self.groups = groups
        self.contacts = contacts
        self.variables = variables

    @classmethod
    def parse_groups(cls, org, json_obj):
        # we actually instantiate our contacts here
        groups = []
        for group_data in json_obj.get(VariableContactAction.GROUPS):
            group_uuid = group_data.get(VariableContactAction.UUID, None)
            group_name = group_data.get(VariableContactAction.NAME)

            # flows from when true deletion was allowed need this
            if not group_name:
                group_name = 'Missing'

            group = ContactGroup.get_or_create(org, org.get_user(), group_name, group_uuid)
            groups.append(group)

        return groups

    @classmethod
    def parse_contacts(cls, org, json_obj):
        contacts = []
        for contact in json_obj.get(VariableContactAction.CONTACTS):
            name = contact.get(VariableContactAction.NAME, None)
            phone = contact.get(VariableContactAction.PHONE, None)
            contact_uuid = contact.get(VariableContactAction.UUID, None)

            contact = Contact.objects.filter(uuid=contact_uuid, org=org).first()
            if not contact and phone:
                contact = Contact.get_or_create(org, org.created_by, name=None, urns=[(TEL_SCHEME, phone)])

                # if they dont have a name use the one in our action
                if name and not contact.name:
                    contact.name = name
                    contact.save(update_fields=['name'])

            if contact:
                contacts.append(contact)

        return contacts

    @classmethod
    def parse_variables(cls, org, json_obj):
        variables = []
        if VariableContactAction.VARIABLES in json_obj:
            variables = list(_.get(VariableContactAction.ID) for _ in json_obj.get(VariableContactAction.VARIABLES))
        return variables

    def build_groups_and_contacts(self, run, msg):
        message_context = run.flow.build_message_context(run.contact, msg)
        contacts = list(self.contacts)
        groups = list(self.groups)

        # see if we've got groups or contacts
        for variable in self.variables:
            # this is a marker for a new contact
            if variable == NEW_CONTACT_VARIABLE:
                # if this is a test contact, stuff a fake contact in for logging purposes
                if run.contact.is_test:
                    contacts.append(Contact(pk=-1))

                # otherwise, really create the contact
                else:
                    contacts.append(Contact.get_or_create(run.flow.org, get_flow_user(), name=None, urns=()))

            # other type of variable, perform our substitution
            else:
                (variable, errors) = Msg.substitute_variables(variable, contact=run.contact,
                                                              message_context=message_context, org=run.flow.org)

                variable_group = ContactGroup.get_user_group(run.flow.org, name=variable)
                if variable_group:
                    groups.append(variable_group)
                else:
                    country = run.flow.org.get_country_code()
                    if country:
                        (number, valid) = URN.normalize_number(variable, country)
                        if number and valid:
                            contact = Contact.get_or_create(run.flow.org, get_flow_user(), urns=[URN.from_tel(number)])
                            contacts.append(contact)

        return groups, contacts


class TriggerFlowAction(VariableContactAction):
    """
    Action that starts a set of contacts down another flow
    """
    TYPE = 'trigger-flow'

    def __init__(self, flow, groups, contacts, variables):
        self.flow = flow
        super(TriggerFlowAction, self).__init__(groups, contacts, variables)

    @classmethod
    def from_json(cls, org, json_obj):
        flow_json = json_obj.get('flow')
        uuid = flow_json.get('uuid')
        flow = Flow.objects.filter(org=org, is_active=True,
                                   is_archived=False, uuid=uuid).first()

        # it is possible our flow got deleted
        if not flow:
            return None

        groups = VariableContactAction.parse_groups(org, json_obj)
        contacts = VariableContactAction.parse_contacts(org, json_obj)
        variables = VariableContactAction.parse_variables(org, json_obj)

        return TriggerFlowAction(flow, groups, contacts, variables)

    def as_json(self):
        contact_ids = [dict(uuid=_.uuid, name=_.name) for _ in self.contacts]
        group_ids = [dict(uuid=_.uuid, name=_.name) for _ in self.groups]
        variables = [dict(id=_) for _ in self.variables]
        return dict(type=TriggerFlowAction.TYPE, flow=dict(uuid=self.flow.uuid, name=self.flow.name),
                    contacts=contact_ids, groups=group_ids, variables=variables)

    def execute(self, run, actionset_uuid, msg, offline_on=None):
        if self.flow:
            message_context = run.flow.build_message_context(run.contact, msg)
            (groups, contacts) = self.build_groups_and_contacts(run, msg)
            # start our contacts down the flow
            if not run.contact.is_test:
                # our extra will be our flow variables in our message context
                extra = message_context.get('extra', dict())
                self.flow.start(groups, contacts, restart_participants=True, started_flows=[run.flow.pk],
                                extra=extra, parent_run=run, interrupt=False)
                return []
            else:
                unique_contacts = set()
                for contact in contacts:
                    unique_contacts.add(contact.pk)

                for group in groups:
                    for contact in group.contacts.all():
                        unique_contacts.add(contact.pk)

                self.logger(run, self.flow, len(unique_contacts))

            return []
        else:  # pragma: no cover
            return []

    def logger(self, run, flow, contact_count):
        log_txt = _("Added %d contact(s) to '%s' flow") % (contact_count, flow.name)
        log = ActionLog.create(run, log_txt)
        return log


class SetLanguageAction(Action):
    """
    Action that sets the language for a contact
    """
    TYPE = 'lang'
    LANG = 'lang'
    NAME = 'name'

    def __init__(self, lang, name):
        self.lang = lang
        self.name = name

    @classmethod
    def from_json(cls, org, json_obj):
        return SetLanguageAction(json_obj.get(cls.LANG), json_obj.get(cls.NAME))

    def as_json(self):
        return dict(type=SetLanguageAction.TYPE, lang=self.lang, name=self.name)

    def execute(self, run, actionset_uuid, msg, offline_on=None):

        if len(self.lang) != 3:
            run.contact.language = None
        else:
            run.contact.language = self.lang

        run.contact.save(update_fields=['language'])
        self.logger(run)
        return []

    def logger(self, run):
        # only log for test contact
        if not run.contact.is_test:
            return False

        log_txt = _("Setting language to %s") % self.name
        log = ActionLog.create(run, log_txt)
        return log


class StartFlowAction(Action):
    """
    Action that starts the contact into another flow
    """
    TYPE = 'flow'
    FLOW = 'flow'
    NAME = 'name'
    UUID = 'uuid'

    def __init__(self, flow):
        self.flow = flow

    @classmethod
    def from_json(cls, org, json_obj):
        flow_obj = json_obj.get(cls.FLOW)
        flow_uuid = flow_obj.get(cls.UUID)

        flow = Flow.objects.filter(org=org, is_active=True, is_archived=False, uuid=flow_uuid).first()

        # it is possible our flow got deleted
        if not flow:
            return None
        else:
            return StartFlowAction(flow)

    def as_json(self):
        return dict(type=StartFlowAction.TYPE, flow=dict(uuid=self.flow.uuid, name=self.flow.name))

    def execute(self, run, actionset_uuid, msg, started_flows, offline_on=None):

        # our extra will be our flow variables in our message context
        message_context = run.flow.build_message_context(run.contact, msg)
        extra = message_context.get('extra', dict())

        # if they are both flow runs, just redirect the call
        if run.flow.flow_type == Flow.VOICE and self.flow.flow_type == Flow.VOICE:
            new_run = self.flow.start([], [run.contact], started_flows=started_flows,
                                      restart_participants=True, extra=extra, parent_run=run)[0]
            url = "https://%s%s" % (settings.TEMBA_HOST, reverse('ivr.ivrcall_handle', args=[new_run.call.pk]))
            run.voice_response.redirect(url)
        else:
            self.flow.start([], [run.contact], started_flows=started_flows, restart_participants=True,
                            extra=extra, parent_run=run)

        self.logger(run)
        return []

    def logger(self, run):
        # only log for test contact
        if not run.contact.is_test:
            return False

        log_txt = _("Starting other flow %s") % self.flow.name

        log = ActionLog.create(run, log_txt)

        return log


class SaveToContactAction(Action):
    """
    Action to save a variable substitution to a field on a contact
    """
    TYPE = 'save'
    FIELD = 'field'
    LABEL = 'label'
    VALUE = 'value'

    def __init__(self, label, field, value):
        self.label = label
        self.field = field
        self.value = value

    @classmethod
    def get_label(cls, org, field, label=None):

        # make sure this field exists
        if field == 'name':
            label = 'Contact Name'
        elif field == 'first_name':
            label = 'First Name'
        elif field == 'tel_e164':
            label = 'Phone Number'
        elif field in ContactURN.CONTEXT_KEYS_TO_SCHEME.keys():
            label = unicode(ContactURN.CONTEXT_KEYS_TO_LABEL[field])
        else:
            contact_field = ContactField.objects.filter(org=org, key=field).first()
            if contact_field:
                label = contact_field.label
            else:
                ContactField.get_or_create(org, get_flow_user(), field, label)

        return label

    @classmethod
    def from_json(cls, org, json_obj):
        # they are creating a new field
        label = json_obj.get(cls.LABEL)
        field = json_obj.get(cls.FIELD)
        value = json_obj.get(cls.VALUE)

        if label and label.startswith('[_NEW_]'):
            label = label[7:]

        # create our contact field if necessary
        if not field:
            field = ContactField.make_key(label)

        # look up our label
        label = cls.get_label(org, field, label)

        return SaveToContactAction(label, field, value)

    def as_json(self):
        return dict(type=SaveToContactAction.TYPE, label=self.label, field=self.field, value=self.value)

    def execute(self, run, actionset_uuid, msg, offline_on=None):
        # evaluate our value
        contact = run.contact
        user = get_flow_user()
        message_context = run.flow.build_message_context(contact, msg)
        (value, errors) = Msg.substitute_variables(self.value, contact, message_context, org=run.flow.org)

        if contact.is_test and errors:
            ActionLog.warn(run, _("Expression contained errors: %s") % ', '.join(errors))

        value = value.strip()

        if self.field == 'name':
            new_value = value[:128]
            contact.name = new_value
            contact.modified_by = user
            contact.save(update_fields=('name', 'modified_by', 'modified_on'))
            self.logger(run, new_value)

        elif self.field == 'first_name':
            new_value = value[:128]
            contact.set_first_name(new_value)
            contact.modified_by = user
            contact.save(update_fields=('name', 'modified_by', 'modified_on'))
            self.logger(run, new_value)

        elif self.field in ContactURN.CONTEXT_KEYS_TO_SCHEME.keys():
            new_value = value[:128]

            # add in our new urn number
            scheme = ContactURN.CONTEXT_KEYS_TO_SCHEME[self.field]

            # trim off '@' for twitter handles
            if self.field == 'twitter':
                if len(new_value) > 0:
                    if new_value[0] == '@':
                        new_value = new_value[1:]

            # only valid urns get added, sorry
            new_urn = URN.normalize(URN.from_parts(scheme, new_value))
            if not URN.validate(new_urn, contact.org.get_country_code()):
                new_urn = None
                if contact.is_test:
                    ActionLog.warn(run, _('Skipping invalid connection for contact (%s:%s)' % (scheme, new_value)))

            if new_urn:
                urns = [urn.urn for urn in contact.urns.all()]
                urns += [new_urn]

                # don't really update URNs on test contacts
                if contact.is_test:
                    ActionLog.info(run, _("Added %s as @contact.%s - skipped in simulator" % (new_value, scheme)))
                else:
                    contact.update_urns(user, urns)

        else:
            new_value = value[:640]
            contact.set_field(user, self.field, new_value)
            self.logger(run, new_value)

        return []

    def logger(self, run, new_value):
        # only log for test contact
        if not run.contact.is_test:
            return False

        label = SaveToContactAction.get_label(run.flow.org, self.field, self.label)
        log_txt = _("Updated %s to '%s'") % (label, new_value)

        log = ActionLog.create(run, log_txt)

        return log


class SetChannelAction(Action):
    """
    Action which sets the preferred channel to use for this Contact. If the contact has no URNs that match
    the Channel being set then this is a no-op.
    """
    TYPE = 'channel'
    CHANNEL = 'channel'
    NAME = 'name'

    def __init__(self, channel):
        self.channel = channel
        super(Action, self).__init__()

    @classmethod
    def from_json(cls, org, json_obj):
        channel_uuid = json_obj.get(SetChannelAction.CHANNEL)

        if channel_uuid:
            channel = Channel.objects.filter(org=org, is_active=True, uuid=channel_uuid).first()
        else:
            channel = None
        return SetChannelAction(channel)

    def as_json(self):
        channel_uuid = self.channel.uuid if self.channel else None
        channel_name = "%s: %s" % (self.channel.get_channel_type_display(), self.channel.get_address_display()) if self.channel else None
        return dict(type=SetChannelAction.TYPE, channel=channel_uuid, name=channel_name)

    def execute(self, run, actionset_uuid, msg, offline_on=None):
        # if we found the channel to set
        if self.channel:
            run.contact.set_preferred_channel(self.channel)
            self.log(run, _("Updated preferred channel to %s") % self.channel.name)
            return []
        else:
            self.log(run, _("Channel not found, no action taken"))
            return []

    def log(self, run, text):  # pragma: no cover
        if run.contact.is_test:
            ActionLog.create(run, text)


class SendAction(VariableContactAction):
    """
    Action which sends a message to a specified set of contacts and groups.
    """
    TYPE = 'send'
    MESSAGE = 'msg'

    def __init__(self, msg, groups, contacts, variables):
        self.msg = msg
        super(SendAction, self).__init__(groups, contacts, variables)

    @classmethod
    def from_json(cls, org, json_obj):
        groups = VariableContactAction.parse_groups(org, json_obj)
        contacts = VariableContactAction.parse_contacts(org, json_obj)
        variables = VariableContactAction.parse_variables(org, json_obj)

        return SendAction(json_obj.get(SendAction.MESSAGE), groups, contacts, variables)

    def as_json(self):
        contact_ids = [dict(uuid=_.uuid) for _ in self.contacts]
        group_ids = [dict(uuid=_.uuid, name=_.name) for _ in self.groups]
        variables = [dict(id=_) for _ in self.variables]
        return dict(type=SendAction.TYPE, msg=self.msg, contacts=contact_ids, groups=group_ids, variables=variables)

    def execute(self, run, actionset_uuid, msg, offline_on=None):
        if self.msg:
            flow = run.flow
            message_context = flow.build_message_context(run.contact, msg)

            (groups, contacts) = self.build_groups_and_contacts(run, msg)

            # create our broadcast and send it
            if not run.contact.is_test:

                # if we have localized versions, add those to our broadcast definition
                language_dict = None
                if isinstance(self.msg, dict):
                    language_dict = json.dumps(self.msg)

                message_text = run.flow.get_localized_text(self.msg)

                # no message text? then no-op
                if not message_text:
                    return list()

                recipients = groups + contacts

                broadcast = Broadcast.create(flow.org, flow.modified_by, message_text, recipients,
                                             language_dict=language_dict)
                broadcast.send(trigger_send=False, message_context=message_context, base_language=flow.base_language)
                return list(broadcast.get_messages())

            else:
                unique_contacts = set()
                for contact in contacts:
                    unique_contacts.add(contact.pk)

                for group in groups:
                    for contact in group.contacts.all():
                        unique_contacts.add(contact.pk)

                # contact refers to each contact this message is being sent to so evaluate without it for logging
                del message_context['contact']

                text = run.flow.get_localized_text(self.msg, run.contact)
                (message, errors) = Msg.substitute_variables(text, None, message_context,
                                                             org=run.flow.org, partial_vars=True)

                self.logger(run, message, len(unique_contacts))

            return []
        else:  # pragma: no cover
            return []

    def logger(self, run, text, contact_count):
        log_txt = _n("Sending '%(msg)s' to %(count)d contact",
                     "Sending '%(msg)s' to %(count)d contacts",
                     contact_count) % dict(msg=text, count=contact_count)
        log = ActionLog.create(run, log_txt)
        return log


class Rule(object):

    def __init__(self, uuid, category, destination, destination_type, test, label=None):
        self.uuid = uuid
        self.category = category
        self.destination = destination
        self.destination_type = destination_type
        self.test = test
        self.label = label

    def get_category_name(self, flow_lang):
        if not self.category:
            if isinstance(self.test, BetweenTest):
                return "%s-%s" % (self.test.min, self.test.max)

        # return the category name for the flow language version
        if isinstance(self.category, dict):
            if flow_lang:
                return self.category[flow_lang]
            else:
                return self.category.values()[0]

        return self.category

    def matches(self, run, sms, context, text):
        return self.test.evaluate(run, sms, context, text)

    def as_json(self):
        return dict(uuid=self.uuid,
                    category=self.category,
                    destination=self.destination,
                    destination_type=self.destination_type,
                    test=self.test.as_json(),
                    label=self.label)

    @classmethod
    def from_json_array(cls, org, json):
        rules = []
        for rule in json:
            category = rule.get('category', None)

            if isinstance(category, dict):
                # prune all of our translations to 36
                for k, v in category.items():
                    if isinstance(v, unicode):
                        category[k] = v[:36]
            elif category:
                category = category[:36]

            destination = rule.get('destination', None)
            destination_type = None

            # determine our destination type, if its not set its an action set
            if destination:
                destination_type = rule.get('destination_type', FlowStep.TYPE_ACTION_SET)

            rules.append(Rule(rule.get('uuid'),
                              category,
                              destination,
                              destination_type,
                              Test.from_json(org, rule['test']),
                              rule.get('label')))

        return rules


class Test(object):
    TYPE = 'type'
    __test_mapping = None

    @classmethod
    def from_json(cls, org, json_dict):
        if not cls.__test_mapping:
            cls.__test_mapping = {
                SubflowTest.TYPE: SubflowTest,
                TrueTest.TYPE: TrueTest,
                FalseTest.TYPE: FalseTest,
                AndTest.TYPE: AndTest,
                OrTest.TYPE: OrTest,
                ContainsTest.TYPE: ContainsTest,
                ContainsAnyTest.TYPE: ContainsAnyTest,
                NumberTest.TYPE: NumberTest,
                LtTest.TYPE: LtTest,
                LteTest.TYPE: LteTest,
                GtTest.TYPE: GtTest,
                GteTest.TYPE: GteTest,
                EqTest.TYPE: EqTest,
                BetweenTest.TYPE: BetweenTest,
                StartsWithTest.TYPE: StartsWithTest,
                HasDateTest.TYPE: HasDateTest,
                DateEqualTest.TYPE: DateEqualTest,
                DateAfterTest.TYPE: DateAfterTest,
                DateBeforeTest.TYPE: DateBeforeTest,
                PhoneTest.TYPE: PhoneTest,
                RegexTest.TYPE: RegexTest,
                HasWardTest.TYPE: HasWardTest,
                HasDistrictTest.TYPE: HasDistrictTest,
                HasStateTest.TYPE: HasStateTest,
                NotEmptyTest.TYPE: NotEmptyTest,
                InterruptTest.TYPE: InterruptTest,
                TimeoutTest.TYPE: TimeoutTest,
                AirtimeStatusTest.TYPE: AirtimeStatusTest,
                WebhookStatusTest.TYPE: WebhookStatusTest,
            }

        type = json_dict.get(cls.TYPE, None)
        if not type:  # pragma: no cover
            raise FlowException("Test definition missing 'type' field: %s", json_dict)

        if type not in cls.__test_mapping:  # pragma: no cover
            raise FlowException("Unknown type: '%s' in definition: %s" % (type, json_dict))

        return cls.__test_mapping[type].from_json(org, json_dict)

    @classmethod
    def from_json_array(cls, org, json):
        tests = []
        for inner in json:
            tests.append(Test.from_json(org, inner))

        return tests

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        """
        Where the work happens, subclasses need to be able to evalute their Test
        according to their definition given the passed in message. Tests do not have
        side effects.
        """
        raise FlowException("Subclasses must implement evaluate, returning a tuple containing 1 or 0 and the value tested")


class WebhookStatusTest(Test):
    """
    {op: 'webhook', status: 'success' }
    """
    TYPE = 'webhook_status'
    STATUS = 'status'

    STATUS_SUCCESS = 'success'
    STATUS_FAILURE = 'failure'

    def __init__(self, status):
        self.status = status

    @classmethod
    def from_json(cls, org, json):
        return WebhookStatusTest(json.get('status'))

    def as_json(self):
        return dict(type=WebhookStatusTest.TYPE, status=self.status)

    def evaluate(self, run, sms, context, text):
        # we treat any 20* return code as successful
        success = 200 <= int(text) < 300

        if success and self.status == WebhookStatusTest.STATUS_SUCCESS:
            return 1, text
        elif not success and self.status == WebhookStatusTest.STATUS_FAILURE:
            return 1, text
        else:
            return 0, None


class AirtimeStatusTest(Test):
    """
    {op: 'airtime_status'}
    """
    TYPE = 'airtime_status'
    EXIT = 'exit_status'

    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'

    STATUS_MAP = {STATUS_SUCCESS: AirtimeTransfer.SUCCESS,
                  STATUS_FAILED: AirtimeTransfer.FAILED}

    def __init__(self, exit_status):
        self.exit_status = exit_status

    @classmethod
    def from_json(cls, org, json):
        return AirtimeStatusTest(json.get('exit_status'))

    def as_json(self):
        return dict(type=AirtimeStatusTest.TYPE, exit_status=self.exit_status)

    def evaluate(self, run, sms, context, text):
        status = text
        if status and AirtimeStatusTest.STATUS_MAP[self.exit_status] == status:
            return 1, status
        return 0, None


class SubflowTest(Test):
    """
    { op: "subflow" }
    """
    TYPE = 'subflow'
    EXIT = 'exit_type'

    TYPE_COMPLETED = 'completed'
    TYPE_EXPIRED = 'expired'

    EXIT_MAP = {TYPE_COMPLETED: FlowRun.EXIT_TYPE_COMPLETED,
                TYPE_EXPIRED: FlowRun.EXIT_TYPE_EXPIRED}

    def __init__(self, exit_type):
        self.exit_type = exit_type

    @classmethod
    def from_json(cls, org, json):
        return SubflowTest(json.get(SubflowTest.EXIT))

    def as_json(self):
        return dict(type=SubflowTest.TYPE, exit_type=self.exit_type)

    def evaluate(self, run, sms, context, text):
        # lookup the subflow run
        subflow_run = FlowRun.objects.filter(parent=run).order_by('-created_on').first()

        if subflow_run and SubflowTest.EXIT_MAP[self.exit_type] == subflow_run.exit_type:
            return 1, text
        return 0, None


class TimeoutTest(Test):
    """
    { op: "timeout", minutes: 60 }
    """
    TYPE = 'timeout'
    MINUTES = 'minutes'

    def __init__(self, minutes):
        self.minutes = minutes

    @classmethod
    def from_json(cls, org, json):
        return TimeoutTest(int(json.get(TimeoutTest.MINUTES)))

    def as_json(self):
        return {'type': TimeoutTest.TYPE, TimeoutTest.MINUTES: self.minutes}

    def evaluate(self, run, sms, context, text):
        if run.timeout_on < timezone.now():
            return 1, None
        else:
            return 0, None


class TrueTest(Test):
    """
    { op: "true" }
    """
    TYPE = 'true'

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return TrueTest()

    def as_json(self):
        return dict(type=TrueTest.TYPE)

    def evaluate(self, run, sms, context, text):
        return 1, text


class FalseTest(Test):
    """
    { op: "false" }
    """
    TYPE = 'false'

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return FalseTest()

    def as_json(self):
        return dict(type=FalseTest.TYPE)

    def evaluate(self, run, sms, context, text):
        return 0, None


class AndTest(Test):
    """
    { op: "and",  "tests": [ ... ] }
    """
    TESTS = 'tests'
    TYPE = 'and'

    def __init__(self, tests):
        self.tests = tests

    @classmethod
    def from_json(cls, org, json):
        return AndTest(Test.from_json_array(org, json[cls.TESTS]))

    def as_json(self):
        return dict(type=AndTest.TYPE, tests=[_.as_json() for _ in self.tests])

    def evaluate(self, run, sms, context, text):
        matches = []
        for test in self.tests:
            (result, value) = test.evaluate(run, sms, context, text)
            if result:
                matches.append(value)
            else:
                return 0, None

        # all came out true, we are true
        return 1, " ".join(matches)


class OrTest(Test):
    """
    { op: "or",  "tests": [ ... ] }
    """
    TESTS = 'tests'
    TYPE = 'or'

    def __init__(self, tests):
        self.tests = tests

    @classmethod
    def from_json(cls, org, json):
        return OrTest(Test.from_json_array(org, json[cls.TESTS]))

    def as_json(self):
        return dict(type=OrTest.TYPE, tests=[_.as_json() for _ in self.tests])

    def evaluate(self, run, sms, context, text):
        for test in self.tests:
            (result, value) = test.evaluate(run, sms, context, text)
            if result:
                return result, value

        return 0, None


class NotEmptyTest(Test):
    """
    { op: "not_empty" }
    """

    TYPE = 'not_empty'

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return NotEmptyTest()

    def as_json(self):
        return dict(type=NotEmptyTest.TYPE)

    def evaluate(self, run, sms, context, text):
        if text and len(text.strip()):
            return 1, text
        return 0, None


class ContainsTest(Test):
    """
    { op: "contains", "test": "red" }
    """
    TEST = 'test'
    TYPE = 'contains'

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        json = dict(type=ContainsTest.TYPE, test=self.test)
        return json

    def test_in_words(self, test, words, raw_words):
        matches = []
        for index, word in enumerate(words):
            if word == test:
                matches.append(index)
                continue

            # words are over 4 characters and start with the same letter
            if len(word) > 4 and len(test) > 4 and word[0] == test[0]:
                # edit distance of 1 or less is a match
                if edit_distance(word, test) <= 1:
                    matches.append(index)

        return matches

    def evaluate(self, run, sms, context, text):
        # substitute any variables
        test = run.flow.get_localized_text(self.test, run.contact)
        test, errors = Msg.substitute_variables(test, run.contact, context, org=run.flow.org)

        # tokenize our test
        tests = regex.split(r"\W+", test.lower(), flags=regex.UNICODE | regex.V0)

        # tokenize our sms
        words = regex.split(r"\W+", text.lower(), flags=regex.UNICODE | regex.V0)
        raw_words = regex.split(r"\W+", text, flags=regex.UNICODE | regex.V0)

        tests = [elt for elt in tests if elt != '']
        words = [elt for elt in words if elt != '']
        raw_words = [elt for elt in raw_words if elt != '']

        # run through each of our tests
        matches = set()
        matched_tests = 0
        for test in tests:
            match = self.test_in_words(test, words, raw_words)
            if match:
                matched_tests += 1
                matches.update(match)

        # we are a match only if every test matches
        if matched_tests == len(tests):
            matches = sorted(list(matches))
            matched_words = " ".join([raw_words[idx] for idx in matches])
            return len(tests), matched_words
        else:
            return 0, None


class ContainsAnyTest(ContainsTest):
    """
    { op: "contains_any", "test": "red" }
    """
    TEST = 'test'
    TYPE = 'contains_any'

    def as_json(self):
        return dict(type=ContainsAnyTest.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):
        # substitute any variables
        test = run.flow.get_localized_text(self.test, run.contact)
        test, errors = Msg.substitute_variables(test, run.contact, context, org=run.flow.org)

        # tokenize our test
        tests = regex.split(r"\W+", test.lower(), flags=regex.UNICODE | regex.V0)

        # tokenize our sms
        words = regex.split(r"\W+", text.lower(), flags=regex.UNICODE | regex.V0)
        raw_words = regex.split(r"\W+", text, flags=regex.UNICODE | regex.V0)

        tests = [elt for elt in tests if elt != '']
        words = [elt for elt in words if elt != '']
        raw_words = [elt for elt in raw_words if elt != '']

        # run through each of our tests
        matches = set()
        for test in tests:
            match = self.test_in_words(test, words, raw_words)
            if match:
                matches.update(match)

        # we are a match if at least one test matches
        if matches:
            matches = sorted(list(matches))
            matched_words = " ".join([raw_words[idx] for idx in matches])
            return 1, matched_words
        else:
            return 0, None


class StartsWithTest(Test):
    """
    { op: "starts", "test": "red" }
    """
    TEST = 'test'
    TYPE = 'starts'

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=StartsWithTest.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):
        # substitute any variables in our test
        test = run.flow.get_localized_text(self.test, run.contact)
        test, errors = Msg.substitute_variables(test, run.contact, context, org=run.flow.org)

        # strip leading and trailing whitespace
        text = text.strip()

        # see whether we start with our test
        if text.lower().find(test.lower()) == 0:
            return 1, text[:len(test)]
        else:
            return 0, None


class HasStateTest(Test):
    TYPE = 'state'

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):
        return dict(type=self.TYPE)

    def evaluate(self, run, sms, context, text):
        org = run.flow.org

        # if they removed their country since adding the rule
        if not org.country:
            return 0, None

        state = org.parse_location(text, STATE_LEVEL)
        if state:
            return 1, state[0]

        return 0, None


class HasDistrictTest(Test):
    TYPE = 'district'
    TEST = 'test'

    def __init__(self, state=None):
        self.state = state

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=self.TYPE, test=self.state)

    def evaluate(self, run, sms, context, text):

        # if they removed their country since adding the rule
        org = run.flow.org
        if not org.country:
            return 0, None

        # evaluate our district in case it has a replacement variable
        state, errors = Msg.substitute_variables(self.state, sms.contact, context, org=run.flow.org)

        parent = org.parse_location(state, STATE_LEVEL)
        if parent:
            district = org.parse_location(text, DISTRICT_LEVEL, parent[0])
            if district:
                return 1, district[0]
        district = org.parse_location(text, DISTRICT_LEVEL)

        # parse location when state contraint is not provided or available
        if (errors or not state) and len(district) == 1:
            return 1, district[0]

        return 0, None


class HasWardTest(Test):
    TYPE = 'ward'
    STATE = 'state'
    DISTRICT = 'district'

    def __init__(self, state=None, district=None):
        self.state = state
        self.district = district

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.STATE], json[cls.DISTRICT])

    def as_json(self):
        return dict(type=self.TYPE, state=self.state, district=self.district)

    def evaluate(self, run, sms, context, text):
        # if they removed their country since adding the rule
        org = run.flow.org
        if not org.country:
            return 0, None
        district = None

        # evaluate our district in case it has a replacement variable
        district_name, missing_district = Msg.substitute_variables(self.district, sms.contact, context, org=run.flow.org)
        state_name, missing_state = Msg.substitute_variables(self.state, sms.contact, context, org=run.flow.org)
        if (district_name and state_name) and (len(missing_district) == 0 and len(missing_state) == 0):
            state = org.parse_location(state_name, STATE_LEVEL)
            if state:
                district = org.parse_location(district_name, DISTRICT_LEVEL, state[0])
                if district:
                    ward = org.parse_location(text, WARD_LEVEL, district[0])
                    if ward:
                        return 1, ward[0]

        # parse location when district contraint is not provided or available
        ward = org.parse_location(text, WARD_LEVEL)
        if len(ward) == 1 and district is None:
            return 1, ward[0]

        return 0, None


class HasDateTest(Test):
    TYPE = 'date'

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):
        return dict(type=self.TYPE)

    def evaluate_date_test(self, message_date):
        return True

    def evaluate(self, run, sms, context, text):
        text = text.replace(' ', "-")
        org = run.flow.org
        dayfirst = org.get_dayfirst()
        tz = org.get_tzinfo()

        (date_format, time_format) = get_datetime_format(dayfirst)

        date = str_to_datetime(text, tz=tz, dayfirst=org.get_dayfirst())
        if date is not None and self.evaluate_date_test(date):
            return 1, datetime_to_str(date, tz=tz, format=time_format, ms=False)

        return 0, None


class DateTest(Test):
    """
    Base class for those tests that check relative dates
    """
    TEST = 'test'
    TYPE = 'date'

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=self.TYPE, test=self.test)

    def evaluate_date_test(self, date_message, date_test):
        raise FlowException("Evaluate date test needs to be defined by subclass.")

    def evaluate(self, run, sms, context, text):
        org = run.flow.org
        dayfirst = org.get_dayfirst()
        tz = org.get_tzinfo()
        test, errors = Msg.substitute_variables(self.test, run.contact, context, org=org)

        text = text.replace(' ', "-")
        if not errors:
            date_message = str_to_datetime(text, tz=tz, dayfirst=dayfirst)
            date_test = str_to_datetime(test, tz=tz, dayfirst=dayfirst)

            (date_format, time_format) = get_datetime_format(dayfirst)

            if date_message is not None and date_test is not None and self.evaluate_date_test(date_message, date_test):
                return 1, datetime_to_str(date_message, tz=tz, format=time_format, ms=False)

        return 0, None


class DateEqualTest(DateTest):
    TEST = 'test'
    TYPE = 'date_equal'

    def evaluate_date_test(self, date_message, date_test):
        return date_message.date() == date_test.date()


class DateAfterTest(DateTest):
    TEST = 'test'
    TYPE = 'date_after'

    def evaluate_date_test(self, date_message, date_test):
        return date_message >= date_test


class DateBeforeTest(DateTest):
    TEST = 'test'
    TYPE = 'date_before'

    def evaluate_date_test(self, date_message, date_test):
        return date_message <= date_test


class NumericTest(Test):
    """
    Base class for those tests that do numeric tests.
    """
    TEST = 'test'
    TYPE = ''

    @classmethod
    def convert_to_decimal(cls, word):
        # common substitutions
        original_word = word
        word = word.replace('l', '1').replace('o', '0').replace('O', '0')

        try:
            return (word, Decimal(word))
        except Exception as e:
            # we only try this hard if we haven't already substituted characters
            if original_word == word:
                # does this start with a number?  just use that part if so
                match = regex.match(r"^(\d+).*$", word, regex.UNICODE | regex.V0)
                if match:
                    return (match.group(1), Decimal(match.group(1)))
                else:
                    raise e
            else:
                raise e

    # test every word in the message against our test
    def evaluate(self, run, sms, context, text):
        text = text.replace(',', '')
        for word in regex.split(r"\s+", text, flags=regex.UNICODE | regex.V0):
            try:
                (word, decimal) = NumericTest.convert_to_decimal(word)
                if self.evaluate_numeric_test(run, context, decimal):
                    return 1, decimal
            except Exception:
                pass
        return 0, None


class BetweenTest(NumericTest):
    """
    Test whether we are between two numbers (inclusive)
    """
    MIN = 'min'
    MAX = 'max'
    TYPE = 'between'

    def __init__(self, min_val, max_val):
        self.min = min_val
        self.max = max_val

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.MIN], json[cls.MAX])

    def as_json(self):
        return dict(type=self.TYPE, min=self.min, max=self.max)

    def evaluate_numeric_test(self, run, context, decimal_value):
        min_val, min_errors = Msg.substitute_variables(self.min, run.contact, context, org=run.flow.org)
        max_val, max_errors = Msg.substitute_variables(self.max, run.contact, context, org=run.flow.org)

        if not min_errors and not max_errors:
            try:
                return Decimal(min_val) <= decimal_value <= Decimal(max_val)
            except Exception:
                pass

        return False


class NumberTest(NumericTest):
    """
    Tests that there is any number in the string.
    """
    TYPE = 'number'

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):
        return dict(type=self.TYPE)

    def evaluate_numeric_test(self, run, context, decimal_value):
        return True


class SimpleNumericTest(Test):
    """
    Base class for those tests that do a numeric test with a single value
    """
    TEST = 'test'
    TYPE = ''

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=self.TYPE, test=self.test)

    def evaluate_numeric_test(self, message_numeric, test_numeric):  # pragma: no cover
        raise FlowException("Evaluate numeric test needs to be defined by subclass")

    # test every word in the message against our test
    def evaluate(self, run, sms, context, text):
        test, errors = Msg.substitute_variables(str(self.test), run.contact, context, org=run.flow.org)

        text = text.replace(',', '')
        for word in regex.split(r"\s+", text, flags=regex.UNICODE | regex.V0):
            try:
                (word, decimal) = NumericTest.convert_to_decimal(word)
                if self.evaluate_numeric_test(decimal, Decimal(test)):
                    return 1, decimal
            except Exception:
                pass
        return 0, None


class GtTest(SimpleNumericTest):
    TEST = 'test'
    TYPE = 'gt'

    def evaluate_numeric_test(self, message_numeric, test_numeric):
        return message_numeric > test_numeric


class GteTest(SimpleNumericTest):
    TEST = 'test'
    TYPE = 'gte'

    def evaluate_numeric_test(self, message_numeric, test_numeric):
        return message_numeric >= test_numeric


class LtTest(SimpleNumericTest):
    TEST = 'test'
    TYPE = 'lt'

    def evaluate_numeric_test(self, message_numeric, test_numeric):
        return message_numeric < test_numeric


class LteTest(SimpleNumericTest):
    TEST = 'test'
    TYPE = 'lte'

    def evaluate_numeric_test(self, message_numeric, test_numeric):
        return message_numeric <= test_numeric


class EqTest(SimpleNumericTest):
    TEST = 'test'
    TYPE = 'eq'

    def evaluate_numeric_test(self, message_numeric, test_numeric):
        return message_numeric == test_numeric


class PhoneTest(Test):
    """
    Test for whether a response contains a phone number
    """
    TYPE = 'phone'

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):
        return dict(type=self.TYPE)

    def evaluate(self, run, sms, context, text):
        org = run.flow.org

        # try to find a phone number in the text we have been sent
        country_code = org.get_country_code()
        if not country_code:
            country_code = 'US'

        number = None
        matches = phonenumbers.PhoneNumberMatcher(text, country_code)

        # try it as an international number if we failed
        if not matches.has_next():
            matches = phonenumbers.PhoneNumberMatcher('+' + text, country_code)

        for match in matches:
            number = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)

        return number, number


class RegexTest(Test):
    """
    Test for whether a response matches a regular expression
    """
    TEST = 'test'
    TYPE = 'regex'

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=self.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):
        try:
            test = run.flow.get_localized_text(self.test, run.contact)

            # check whether we match
            rexp = regex.compile(test, regex.UNICODE | regex.IGNORECASE | regex.MULTILINE | regex.V0)
            match = rexp.search(text)

            # if so, $0 will be what we return
            if match:
                return_match = match.group(0)

                # build up a dictionary that contains indexed values
                group_dict = match.groupdict()
                for idx in range(rexp.groups + 1):
                    group_dict[str(idx)] = match.group(idx)

                # set it on run@extra
                run.update_fields(group_dict)

                # return all matched values
                return True, return_match

        except Exception:
            import traceback
            traceback.print_exc()

        return False, None


class InterruptTest(Test):
    """
    Test if it's an interrupt status message
    """
    TYPE = "interrupted_status"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):
        return dict(type=self.TYPE)

    def evaluate(self, run, msg, context, text):
        return (True, self.TYPE) if msg.status == INTERRUPTED else (False, None)
