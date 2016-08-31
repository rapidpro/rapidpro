from __future__ import unicode_literals

import copy
import json

from temba.flows.models import ContainsTest, StartsWithTest, ContainsAnyTest, RegexTest, ReplyAction
from temba.flows.models import SayAction, SendAction, RuleSet
from temba.utils.expressions import migrate_template
from uuid import uuid4
import regex


def migrate_to_version_10(json_flow, flow):
    """
    Looks for webhook ruleset_types, adding success and failure cases and moving
    webhook_action and webhook to config
    """
    def replace_webhook_ruleset(ruleset, base_lang):
        # not a webhook? delete any turds of webhook or webhook_action
        if ruleset.get('ruleset_type', None) != 'webhook':
            ruleset.pop('webhook_action', None)
            ruleset.pop('webhook', None)
            return ruleset

        if 'config' not in ruleset:
            ruleset['config'] = dict()

        # webhook_action and webhook now live in config
        ruleset['config']['webhook_action'] = ruleset['webhook_action']
        del ruleset['webhook_action']
        ruleset['config']['webhook'] = ruleset['webhook']
        del ruleset['webhook']

        # we now can route differently on success and failure, route old flows to the same destination
        # for both
        destination = ruleset['rules'][0].get('destination', None)
        destination_type = ruleset['rules'][0].get('destination_type', None)
        old_rule_uuid = ruleset['rules'][0]['uuid']

        rules = []
        for status in ['success', 'failure']:
            # maintain our rule uuid for the success case
            rule_uuid = old_rule_uuid if status == 'success' else unicode(uuid4())
            new_rule = dict(test=dict(status=status, type='webhook_status'),
                            category={base_lang: status.capitalize()},
                            uuid=rule_uuid)

            if destination:
                new_rule['destination'] = destination
                new_rule['destination_type'] = destination_type

            rules.append(new_rule)

        ruleset['rules'] = rules
        return ruleset

    # if we have rulesets, we need to fix those up with our new webhook types
    base_lang = json_flow.get('base_language', 'base')
    if 'rule_sets' in json_flow:
        rulesets = []
        for ruleset in json_flow['rule_sets']:
            ruleset = replace_webhook_ruleset(ruleset, base_lang)
            rulesets.append(ruleset)

        json_flow['rule_sets'] = rulesets

    return json_flow


def migrate_export_to_version_9(exported_json, org, same_site=True):
    """
    Migrates remaining ids to uuids. Changes to uuids for Flows, Groups,
    Contacts and Channels inside of Actions, Triggers, Campaigns, Events
    """

    def replace(str, match, replace):
        rexp = regex.compile(match, flags=regex.MULTILINE | regex.UNICODE | regex.V0)

        # replace until no matches found
        matches = 1
        while matches:
            (str, matches) = rexp.subn(replace, str)

        return str

    exported_string = json.dumps(exported_json)

    # any references to @extra.flow are now just @parent
    exported_string = replace(exported_string, '@(extra\.flow)', '@parent')
    exported_string = replace(exported_string, '(@\(.*?)extra\.flow(.*?\))', r'\1parent\2')

    # any references to @extra.contact are now @parent.contact
    exported_string = replace(exported_string, '@(extra\.contact)', '@parent.contact')
    exported_string = replace(exported_string, '(@\(.*?)extra\.contact(.*?\))', r'\1parent.contact\2')

    exported_json = json.loads(exported_string)

    flow_id_map = {}
    group_id_map = {}
    contact_id_map = {}
    campaign_id_map = {}
    campaign_event_id_map = {}
    label_id_map = {}

    def get_uuid(id_map, obj_id):
        uuid = id_map.get(obj_id, None)
        if not uuid:
            uuid = unicode(uuid4())
            id_map[obj_id] = uuid
        return uuid

    def replace_with_uuid(ele, manager, id_map, nested_name=None, obj=None, create_dict=False):
        # deal with case of having only a string and no name
        if isinstance(ele, basestring) and create_dict:
            # variable references should just stay put
            if len(ele) > 0 and ele[0] == '@':
                return ele
            else:
                ele = dict(name=ele)

        obj_id = ele.pop('id', None)
        obj_name = ele.pop('name', None)

        if same_site and not obj and obj_id:
            try:
                obj = manager.filter(pk=obj_id, org=org).first()
            except:
                pass

        # nest it if we were given a nested name
        if nested_name:
            ele[nested_name] = dict()
            ele = ele[nested_name]

        if obj:
            ele['uuid'] = obj.uuid

            if obj.name:
                ele['name'] = obj.name
        else:
            if obj_id:
                ele['uuid'] = get_uuid(id_map, obj_id)

            if obj_name:
                ele['name'] = obj_name

        return ele

    def remap_flow(ele, nested_name=None):
        from temba.flows.models import Flow
        replace_with_uuid(ele, Flow.objects, flow_id_map, nested_name)

    def remap_group(ele):
        from temba.contacts.models import ContactGroup
        return replace_with_uuid(ele, ContactGroup.user_groups, group_id_map, create_dict=True)

    def remap_campaign(ele):
        from temba.campaigns.models import Campaign
        replace_with_uuid(ele, Campaign.objects, campaign_id_map)

    def remap_campaign_event(ele):
        from temba.campaigns.models import CampaignEvent
        event = None
        if same_site:
            event = CampaignEvent.objects.filter(pk=ele['id'], campaign__org=org).first()
        replace_with_uuid(ele, CampaignEvent.objects, campaign_event_id_map, obj=event)

    def remap_contact(ele):
        from temba.contacts.models import Contact
        replace_with_uuid(ele, Contact.objects, contact_id_map)

    def remap_channel(ele):
        from temba.channels.models import Channel
        channel_id = ele.get('channel')
        if channel_id:
            channel = Channel.objects.filter(pk=channel_id).first()
            if channel:
                ele['channel'] = channel.uuid

    def remap_label(ele):
        from temba.msgs.models import Label
        replace_with_uuid(ele, Label.label_objects, label_id_map)

    for flow in exported_json.get('flows', []):
        for action_set in flow['action_sets']:
            for action in action_set['actions']:
                if action['type'] in ('add_group', 'del_group', 'send', 'trigger-flow'):
                    groups = []
                    for group_json in action.get('groups', []):
                        groups.append(remap_group(group_json))
                    for contact_json in action.get('contacts', []):
                        remap_contact(contact_json)
                    if groups:
                        action['groups'] = groups
                if action['type'] in ('trigger-flow', 'flow'):
                    remap_flow(action, 'flow')
                if action['type'] == 'add_label':
                    for label in action.get('labels', []):
                        remap_label(label)

        metadata = flow['metadata']
        if 'id' in metadata:
            if metadata.get('id', None):
                remap_flow(metadata)
            else:
                del metadata['id']

    for trigger in exported_json.get('triggers', []):
        if 'flow' in trigger:
            remap_flow(trigger['flow'])
        for group in trigger['groups']:
            remap_group(group)
        remap_channel(trigger)

    for campaign in exported_json.get('campaigns', []):
        remap_campaign(campaign)
        remap_group(campaign['group'])
        for event in campaign.get('events', []):
            remap_campaign_event(event)
            if 'id' in event['relative_to']:
                del event['relative_to']['id']
            if 'flow' in event:
                remap_flow(event['flow'])

    return exported_json


def migrate_to_version_9(json_flow, flow):
    """
    This version marks the first usage of subflow rulesets. Moves more items to UUIDs.
    """
    # inject metadata if it's missing
    from temba.flows.models import Flow
    if Flow.METADATA not in json_flow:
        json_flow[Flow.METADATA] = flow.get_metadata()
    return migrate_export_to_version_9(dict(flows=[json_flow]), flow.org)['flows'][0]


def migrate_to_version_8(json_flow, flow=None):
    """
    Migrates any expressions found in the flow definition to use the new @(...) syntax
    """
    def migrate_node(node):
        if isinstance(node, basestring):
            return migrate_template(node)
        if isinstance(node, list):
            for n in range(len(node)):
                node[n] = migrate_node(node[n])
        if isinstance(node, dict):
            for key, val in node.iteritems():
                node[key] = migrate_node(val)
        return node

    for rule_set in json_flow.get('rule_sets', []):
        for rule in rule_set['rules']:
            migrate_node(rule['test'])

        if 'operand' in rule_set and rule_set['operand']:
            rule_set['operand'] = migrate_node(rule_set['operand'])
        if 'webhook' in rule_set and rule_set['webhook']:
            rule_set['webhook'] = migrate_node(rule_set['webhook'])

    for action_set in json_flow.get('action_sets', []):
        for action in action_set['actions']:
            migrate_node(action)

    return json_flow


def migrate_to_version_7(json_flow, flow=None):
    """
    Adds flow details to metadata section
    """
    definition = json_flow.get('definition', None)

    # don't attempt if there isn't a nested definition block
    if definition:
        definition['flow_type'] = json_flow.get('flow_type', 'F')
        metadata = definition.get('metadata', None)
        if not metadata:
            metadata = dict()
            definition['metadata'] = metadata

        metadata['name'] = json_flow.get('name')
        metadata['id'] = json_flow.get('id', None)
        metadata['uuid'] = json_flow.get('uuid', None)
        revision = json_flow.get('revision', None)
        if revision:
            metadata['revision'] = revision
        metadata['saved_on'] = json_flow.get('last_saved')

        # single message flows incorrectly created an empty rulesets
        # element which should be rule_sets instead
        if 'rulesets' in definition:
            definition.pop('rulesets')

        return definition

    return json_flow


def migrate_to_version_6(json_flow, flow=None):
    """
    This migration removes the non-localized flow format. This means all potentially localizable
    text will be a dict from the outset. If no language is set, we will use 'base' as the
    default language.
    """

    definition = json_flow.get('definition')

    # the name of the base language if its not set yet
    base_language = 'base'

    def convert_to_dict(d, key):
        if key not in d:
            raise ValueError("Missing '%s' in dict: %s" % (key, d))

        if not isinstance(d[key], dict):
            d[key] = {base_language: d[key]}

    if 'base_language' not in definition:
        definition['base_language'] = base_language

        for ruleset in definition.get('rule_sets', []):
            for rule in ruleset.get('rules'):

                # betweens haven't always required a category name, create one
                rule_test = rule['test']
                if rule_test['type'] == 'between' and 'category' not in rule:
                    rule['category'] = '%s-%s' % (rule_test['min'], rule_test['max'])

                # convert the category name
                convert_to_dict(rule, 'category')

                # convert our localized types
                if (rule['test']['type'] in [ContainsTest.TYPE, ContainsAnyTest.TYPE,
                                             StartsWithTest.TYPE, RegexTest.TYPE]):
                    convert_to_dict(rule['test'], 'test')

        for actionset in definition.get('action_sets'):
            for action in actionset.get('actions'):
                if action['type'] in [SendAction.TYPE, ReplyAction.TYPE, SayAction.TYPE]:
                    convert_to_dict(action, 'msg')
                if action['type'] == SayAction.TYPE:
                    if 'recording' in action:
                        convert_to_dict(action, 'recording')
    return json_flow


def migrate_to_version_5(json_flow, flow=None):
    """
    Adds passive rulesets. This necessitates injecting nodes in places where
    we were previously waiting implicitly with explicit waits.
    """

    def requires_step(operand):

        # if we start with =( then we are an expression
        is_expression = operand and len(operand) > 2 and operand[0:2] == '=('
        if '@step' in operand or (is_expression and 'step' in operand):
            return True
        return False

    definition = json_flow.get('definition')

    for ruleset in definition.get('rule_sets', []):

        response_type = ruleset.pop('response_type', None)
        ruleset_type = ruleset.get('ruleset_type', None)
        label = ruleset.get('label')

        # remove config from any rules, these are turds
        for rule in ruleset.get('rules'):
            if 'config' in rule:
                del rule['config']

        if response_type and not ruleset_type:

            # webhooks now live in their own ruleset, insert one
            webhook_url = ruleset.pop('webhook', None)
            webhook_action = ruleset.pop('webhook_action', None)

            has_old_webhook = webhook_url and ruleset_type != RuleSet.TYPE_WEBHOOK

            # determine our type from our operand
            operand = ruleset.get('operand')
            if not operand:
                operand = '@step.value'

            operand = operand.strip()

            # all previous ruleset that require step should be wait_message
            if requires_step(operand):
                # if we have an empty operand, go ahead and update it
                if not operand:
                    ruleset['operand'] = '@step.value'

                if response_type == 'K':
                    ruleset['ruleset_type'] = RuleSet.TYPE_WAIT_DIGITS
                elif response_type == 'M':
                    ruleset['ruleset_type'] = RuleSet.TYPE_WAIT_DIGIT
                elif response_type == 'R':
                    ruleset['ruleset_type'] = RuleSet.TYPE_WAIT_RECORDING
                else:

                    if operand == '@step.value':
                        ruleset['ruleset_type'] = RuleSet.TYPE_WAIT_MESSAGE
                    else:

                        ruleset['ruleset_type'] = RuleSet.TYPE_EXPRESSION

                        # if it's not a plain split, make us wait and create
                        # an expression split node to handle our response
                        pausing_ruleset = copy.deepcopy(ruleset)
                        pausing_ruleset['ruleset_type'] = RuleSet.TYPE_WAIT_MESSAGE
                        pausing_ruleset['operand'] = '@step.value'
                        pausing_ruleset['label'] = label + ' Response'
                        remove_extra_rules(definition, pausing_ruleset)
                        insert_node(definition, pausing_ruleset, ruleset)

            else:
                # if there's no reference to step, figure out our type
                ruleset['ruleset_type'] = RuleSet.TYPE_EXPRESSION
                # special case contact and flow fields
                if ' ' not in operand and '|' not in operand:
                    if operand == '@contact.groups':
                        ruleset['ruleset_type'] = RuleSet.TYPE_EXPRESSION
                    elif operand.find('@contact.') == 0:
                        ruleset['ruleset_type'] = RuleSet.TYPE_CONTACT_FIELD
                    elif operand.find('@flow.') == 0:
                        ruleset['ruleset_type'] = RuleSet.TYPE_FLOW_FIELD

                # we used to stop at webhooks, now we need a new node
                # to make sure processing stops at this step now
                if has_old_webhook:
                    pausing_ruleset = copy.deepcopy(ruleset)
                    pausing_ruleset['ruleset_type'] = RuleSet.TYPE_WAIT_MESSAGE
                    pausing_ruleset['operand'] = '@step.value'
                    pausing_ruleset['label'] = label + ' Response'
                    remove_extra_rules(definition, pausing_ruleset)
                    insert_node(definition, pausing_ruleset, ruleset)

            # finally insert our webhook node if necessary
            if has_old_webhook:
                webhook_ruleset = copy.deepcopy(ruleset)
                webhook_ruleset['webhook'] = webhook_url
                webhook_ruleset['webhook_action'] = webhook_action
                webhook_ruleset['operand'] = '@step.value'
                webhook_ruleset['ruleset_type'] = RuleSet.TYPE_WEBHOOK
                webhook_ruleset['label'] = label + ' Webhook'
                remove_extra_rules(definition, webhook_ruleset)
                insert_node(definition, webhook_ruleset, ruleset)

    return json_flow


# ================================ Helper methods for flow migrations ===================================

def get_entry(json_flow):
    """
    Returns the entry node for the passed in flow, this is the ruleset or actionset with the lowest y
    """
    lowest_y = None
    lowest_uuid = None

    for ruleset in json_flow.get('rule_sets', []):
        if lowest_y is None or ruleset['y'] < lowest_y:
            lowest_uuid = ruleset['uuid']
            lowest_y = ruleset['y']

    for actionset in json_flow.get('action_sets', []):
        if lowest_y is None or actionset['y'] <= lowest_y:
            lowest_uuid = actionset['uuid']
            lowest_y = actionset['y']

    return lowest_uuid


def map_actions(json_flow, fixer_method):
    """
    Given a JSON flow, runs fixer_method on every action. If fixer_method returns None, the action is
    removed, otherwise the returned action is used.
    """
    action_sets = []
    for actionset in json_flow.get('action_sets', []):
        actions = []
        for action in actionset.get('actions', []):
            fixed_action = fixer_method(action)
            if fixed_action is not None:
                actions.append(fixed_action)

        actionset['actions'] = actions

        # only add in this actionset if there are actions in it
        if actions:
            action_sets.append(actionset)

    json_flow['action_sets'] = action_sets
    json_flow['entry'] = get_entry(json_flow)

    return json_flow


def remove_extra_rules(json_flow, ruleset):
    """ Remove all rules but the all responses rule """
    rules = []
    old_rules = ruleset.get('rules')
    for rule in old_rules:
        if rule['test']['type'] == 'true':
            if 'base_language' in json_flow:
                rule['category'][json_flow['base_language']] = 'All Responses'
            else:
                rule['category'] = 'All Responses'
            rules.append(rule)

    ruleset['rules'] = rules


def insert_node(flow, node, _next):
    """ Inserts a node right before _next """

    def update_destination(node_to_update, uuid):
        if node_to_update.get('actions', []):
            node_to_update['destination'] = uuid
        else:
            for rule in node_to_update.get('rules', []):
                rule['destination'] = uuid

    # make sure we have a fresh uuid
    node['uuid'] = _next['uuid']
    _next['uuid'] = unicode(uuid4())
    update_destination(node, _next['uuid'])

    # bump everybody down
    for actionset in flow.get('action_sets'):
        if actionset.get('y') >= node.get('y'):
            actionset['y'] += 100

    for ruleset in flow.get('rule_sets'):
        if ruleset.get('y') >= node.get('y'):
            ruleset['y'] += 100

    # we are an actionset
    if node.get('actions', []):
        node.destination = _next.uuid
        flow['action_sets'].append(node)

    # otherwise point all rules to the same place
    else:
        for rule in node.get('rules', []):
            rule['destination'] = _next['uuid']
        flow['rule_sets'].append(node)
