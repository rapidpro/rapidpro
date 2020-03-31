import copy
from collections import defaultdict
from uuid import uuid4

import regex

from temba.contacts.models import ContactField, ContactGroup
from temba.flows.models import Flow, RuleSet
from temba.msgs.models import Label
from temba.utils import json
from temba.utils.languages import iso6392_to_iso6393

from .definition import (
    ContainsAnyTest,
    ContainsTest,
    InGroupTest,
    RegexTest,
    ReplyAction,
    SayAction,
    SendAction,
    StartFlowAction,
    StartsWithTest,
    TriggerFlowAction,
    VariableContactAction,
)
from .expressions import migrate_v7_template


def migrate_to_version_11_12(json_flow, flow=None):
    """
    This removes actions with invalid channel references
    """
    # this migration only matters for existing flows
    if not flow:
        return json_flow

    new_flow_json = json_flow.copy()
    new_flow_json[Flow.ACTION_SETS] = []

    entry = json_flow.get(Flow.ENTRY)
    action_sets = json_flow.get(Flow.ACTION_SETS, [])
    reroute_uuid_remap = {}
    needs_move_entry = False

    for actionset_index, action_set in enumerate(action_sets):
        action_set_clone = action_set.copy()
        valid_actions = []

        for action_index, action in enumerate(action_set["actions"]):
            if action.get("type") == "channel":
                channel = None
                channel_uuid = action.get("channel")
                channel_name = action.get("name")
                if channel_uuid is not None:
                    channel = flow.org.channels.filter(is_active=True, uuid=channel_uuid).first()

                if channel is None and channel_name is not None:
                    channel = flow.org.channels.filter(is_active=True, name=channel_name).first()

                if channel is None:
                    # skip this action it is invalid
                    continue
                else:
                    action["channel"] = channel.uuid
                    action["name"] = "%s: %s" % (channel.get_channel_type_display(), channel.get_address_display())

            # the action is valid append it
            valid_actions.append(action)

        action_set_clone["actions"] = valid_actions
        if len(valid_actions) > 0:
            new_flow_json[Flow.ACTION_SETS].append(action_set_clone)
        else:
            reroute_uuid_remap[action_set["uuid"]] = action_set.get("destination")
            needs_move_entry = True

    action_sets = new_flow_json.get(Flow.ACTION_SETS, [])
    rule_sets = new_flow_json.get(Flow.RULE_SETS, [])

    rerouted_sources = reroute_uuid_remap.keys()
    for source_uuid in rerouted_sources:
        reroute_destination = reroute_uuid_remap[source_uuid]
        # Check final destination not rerouted
        while reroute_destination in rerouted_sources:
            reroute_destination = reroute_uuid_remap[reroute_destination]
        reroute_uuid_remap[source_uuid] = reroute_destination

    if entry in reroute_uuid_remap:
        entry = reroute_uuid_remap[entry]
        new_flow_json[Flow.ENTRY] = entry

    for actionset_index, action_set in enumerate(action_sets):
        if action_set.get("destination") in reroute_uuid_remap:
            new_flow_json[Flow.ACTION_SETS][actionset_index]["destination"] = reroute_uuid_remap[
                action_set["destination"]
            ]

        if needs_move_entry and action_set["uuid"] == entry:
            action_set["y"] = 0

    for ruleset_index, rule_set in enumerate(rule_sets):
        for rule_index, rule in enumerate(rule_set.get(Flow.RULES)):
            if rule.get("destination") in reroute_uuid_remap:
                new_flow_json[Flow.RULE_SETS][ruleset_index][Flow.RULES][rule_index][
                    "destination"
                ] = reroute_uuid_remap[rule["destination"]]

    return new_flow_json


def migrate_to_version_11_11(json_flow, flow=None):
    """
    Versions before 11.11 maintained uuid mismatches on imported flows. This updates
    the flow definition with accurate label uuids
    """

    # this migration only matters for existing flows
    if not flow:
        return json_flow

    # only look up label once per flow migration
    uuid_map = {}

    def remap_label(label):
        # labels can be single string expressions
        if type(label) is dict:
            # we haven't been mapped yet (also, non-uuid labels can't be mapped)
            if ("uuid" not in label or label["uuid"] not in uuid_map) and Label.is_valid_name(label["name"]):
                label_instance = Label.get_or_create(flow.org, flow.created_by, label["name"])

                # map label references that started with a uuid
                if "uuid" in label:
                    uuid_map[label["uuid"]] = label_instance.uuid

                label["uuid"] = label_instance.uuid

            # we were already mapped
            elif label["uuid"] in uuid_map:
                label["uuid"] = uuid_map[label["uuid"]]

    for actionset in json_flow.get(Flow.ACTION_SETS, []):
        for action in actionset[Flow.ACTIONS]:
            for label in action.get("labels", []):
                remap_label(label)

    return json_flow


def migrate_export_to_version_11_10(exported_json, org, same_site=True):
    """
    Migrates an export of potentially multiple flows to 11.10
    """

    # need to provide the types of all flows in this export to migrate_to_version_11_10 which
    # otherwise can only find types of flows in the database
    flow_types = {f["metadata"]["uuid"]: f["flow_type"] for f in exported_json.get("flows", [])}

    migrated_flows = []
    for flow in exported_json.get("flows", []):
        flow = migrate_to_version_11_10(flow, flow_types=flow_types)
        migrated_flows.append(flow)

    exported_json["flows"] = migrated_flows
    return exported_json


def migrate_to_version_11_10(json_flow, flow=None, flow_types=None):
    """
    Replaces any StartFlowAction which crosses modalities with a TriggerFlowAction
    """

    # some "join group" flows are missing type
    if not json_flow.get("flow_type"):  # pragma: needs cover
        json_flow["flow_type"] = Flow.TYPE_MESSAGE

    # cache of flow uuid to type
    if not flow_types:
        flow_types = {}

    # need to compare flow types with F and M considered equal
    def flow_types_eq(t1, t2):
        return (t1 == t2) or (t1 == "F" and t2 == "M") or (t1 == "M" and t2 == "F")

    def get_flow_type(flow_uuid):
        if flow_uuid not in flow_types:
            f = Flow.objects.filter(uuid=flow_uuid).only("flow_type").first()
            flow_types[flow_uuid] = f.flow_type if f else None
        return flow_types[flow_uuid]

    if Flow.ACTION_SETS not in json_flow:  # pragma: no cover
        json_flow[Flow.ACTION_SETS] = []
    if Flow.RULE_SETS not in json_flow:
        json_flow[Flow.RULE_SETS] = []

    # replace any StartFlowAction pointing to a flow of a different modality
    for action_set in json_flow[Flow.ACTION_SETS]:
        for action in action_set.get("actions", []):
            if action["type"] == StartFlowAction.TYPE:
                subflow_type = get_flow_type(action["flow"]["uuid"])
                if subflow_type and not flow_types_eq(subflow_type, json_flow["flow_type"]):
                    action["type"] = TriggerFlowAction.TYPE
                    action["contacts"] = []
                    action["groups"] = []
                    action["urns"] = []
                    action["variables"] = [{VariableContactAction.ID: "@contact.uuid"}]

    del_rule_sets = []

    # replace any subflow ruleset pointing to a flow of a different modality
    for rule_set in json_flow.get(Flow.RULE_SETS, []):
        if rule_set["ruleset_type"] == RuleSet.TYPE_SUBFLOW:
            subflow_type = get_flow_type(rule_set["config"]["flow"]["uuid"])
            if subflow_type and not flow_types_eq(subflow_type, json_flow["flow_type"]):

                # create new action set in same place with same connections
                json_flow[Flow.ACTION_SETS].append(
                    {
                        "uuid": rule_set["uuid"],
                        "x": rule_set.get("x"),
                        "y": rule_set.get("y"),
                        "destination": rule_set["rules"][0].get("destination"),
                        "actions": [
                            {
                                "type": TriggerFlowAction.TYPE,
                                "uuid": str(uuid4()),
                                "flow": rule_set["config"]["flow"],
                                "contacts": [],
                                "groups": [],
                                "urns": [],
                                "variables": [{VariableContactAction.ID: "@contact.uuid"}],
                            }
                        ],
                        "exit_uuid": rule_set["rules"][0]["uuid"],
                    }
                )

                del_rule_sets.append(rule_set["uuid"])

    # remove any rulesets that were replaced
    json_flow[Flow.RULE_SETS] = [rs for rs in json_flow[Flow.RULE_SETS] if rs["uuid"] not in del_rule_sets]

    return json_flow


def migrate_to_version_11_9(json_flow, flow=None):
    """
    Remove actions and rulesets that have references to invalid flows (is_active=False, is_archived=True)
    """

    # this migration only matters for existing flows
    # we don't want to migrate flows which are about to be imported
    if not flow:
        return json_flow

    main_flow_uuid = json_flow.get("metadata", {}).get("uuid", None)
    action_sets = json_flow.get(Flow.ACTION_SETS, [])
    rule_sets = json_flow.get(Flow.RULE_SETS, [])

    detected_flows = set()

    for action_set in action_sets:
        for action in action_set["actions"]:
            if action["type"] == StartFlowAction.TYPE:
                flow_uuid = action["flow"]["uuid"]
                detected_flows.add(flow_uuid)

            if action["type"] == TriggerFlowAction.TYPE:
                flow_uuid = action["flow"]["uuid"]
                detected_flows.add(flow_uuid)

    for rule_set in rule_sets:
        if rule_set["ruleset_type"] == RuleSet.TYPE_SUBFLOW:
            flow_uuid = rule_set["config"]["flow"]["uuid"]
            detected_flows.add(flow_uuid)

    invalid_flow_uuids = set()
    if detected_flows:
        valid_flow_uuids = {
            flow_uuid
            for flow_uuid in Flow.objects.filter(
                uuid__in=detected_flows, is_active=True, is_archived=False
            ).values_list("uuid", flat=True)
        }
        invalid_flow_uuids = detected_flows.difference(valid_flow_uuids)

    # get the copy of the flow
    new_flow_json = json_flow.copy()
    total_removed_actions = 0
    total_removed_rulesets = 0

    if invalid_flow_uuids:
        # remove invalid actions and rulesets
        for actionset_index, action_set in enumerate(action_sets):
            for action_index, action in enumerate(action_set["actions"]):
                if action["type"] == StartFlowAction.TYPE:
                    flow_uuid = action["flow"]["uuid"]
                    if flow_uuid in invalid_flow_uuids:

                        del new_flow_json[Flow.ACTION_SETS][actionset_index]["actions"][action_index]
                        total_removed_actions += 1

                if action["type"] == TriggerFlowAction.TYPE:
                    flow_uuid = action["flow"]["uuid"]
                    if flow_uuid in invalid_flow_uuids:

                        del new_flow_json[Flow.ACTION_SETS][actionset_index]["actions"][action_index]
                        total_removed_actions += 1

        for ruleset_index, rule_set in enumerate(rule_sets):
            if rule_set["ruleset_type"] == RuleSet.TYPE_SUBFLOW:
                flow_uuid = rule_set["config"]["flow"]["uuid"]

                if flow_uuid in invalid_flow_uuids:

                    del new_flow_json[Flow.RULE_SETS][ruleset_index]
                    total_removed_rulesets += 1

    if total_removed_actions + total_removed_rulesets > 0:
        print(f"Flow {main_flow_uuid}: removed {total_removed_actions} actions and {total_removed_rulesets} rulesets")

    return new_flow_json


def migrate_to_version_11_8(json_flow, flow=None):
    """
    Fixes duplicate rule UUIDs
    """
    seen_uuids = set()

    for rs in json_flow.get(Flow.RULE_SETS, []):
        for rule in rs.get("rules"):
            if rule.get("uuid") in seen_uuids or not rule.get("uuid"):
                rule["uuid"] = str(uuid4())
            seen_uuids.add(rule["uuid"])

    return json_flow


def migrate_to_version_11_7(json_flow, flow=None):
    """
    Replaces webhook actions with rulesets. Requires splitting up nodes where the action sits alongside other actions.
    """

    # need a lookup of all nodes to resolve destinations
    nodes_by_uuid = {}
    for node in json_flow.get(Flow.ACTION_SETS, []) + json_flow.get(Flow.RULE_SETS, []):
        nodes_by_uuid[node["uuid"]] = node

    # map of actionset UUIDs to a list of the nodes replacing it
    node_replacements = defaultdict(list)

    # for creating unique ruleset labels
    num_new_rulesets = 0

    for actionset in json_flow.get(Flow.ACTION_SETS, []):
        # split actions into a list of 1) single webhook actions 2) lists of non-webhook actions
        new_sets = []
        has_webooks = False
        for action in actionset[Flow.ACTIONS]:
            if action["type"] == "api":
                new_sets.append(action)
                has_webooks = True
            else:
                if len(new_sets) == 0 or not isinstance(new_sets[-1], list):
                    new_sets.append([])
                new_sets[-1].append(action)

        if not has_webooks:
            continue

        destination = nodes_by_uuid.get(actionset["destination"]) if actionset.get("destination") else None

        for (i, new_set) in reversed(list(enumerate(new_sets))):
            # if this is first new node, it gets the UUID of the actionset being
            # replaced so that nodes pointing to this actionset will now point to it
            new_node_uuid = actionset["uuid"] if i == 0 else str(uuid4())

            if destination:
                destination_uuid = destination["uuid"]
                destination_type = "A" if "actions" in destination else "R"
            else:
                destination_uuid = None
                destination_type = None

            if isinstance(new_set, dict):
                old_action = new_set
                num_new_rulesets += 1

                new_node = {
                    "uuid": new_node_uuid,
                    "x": actionset.get("x", 0),
                    "y": actionset.get("y", 0),
                    "label": f"Migrated Webhook {num_new_rulesets}",
                    "rules": [
                        {
                            "uuid": str(uuid4()),
                            "category": {json_flow["base_language"]: "Success"},
                            "destination": destination_uuid,
                            "destination_type": destination_type,
                            "test": {"type": "webhook_status", "status": "success"},
                            "label": None,
                        },
                        {
                            "uuid": str(uuid4()),
                            "category": {json_flow["base_language"]: "Failure"},
                            "destination": destination_uuid,
                            "destination_type": destination_type,
                            "test": {"type": "webhook_status", "status": "failure"},
                            "label": None,
                        },
                    ],
                    "finished_key": None,
                    "ruleset_type": "webhook",
                    "response_type": "",
                    "operand": "@step.value",
                    "config": {
                        "webhook": old_action.get("webhook", ""),
                        "webhook_action": old_action.get("action", "POST"),
                        "webhook_headers": old_action.get("webhook_headers", []),
                    },
                }

                if Flow.RULE_SETS not in json_flow:  # pragma: no cover
                    json_flow[Flow.RULE_SETS] = []

                json_flow[Flow.RULE_SETS].append(new_node)
            else:
                new_node = {
                    "uuid": new_node_uuid,
                    "x": actionset.get("x", 0),
                    "y": actionset.get("y", 0),
                    "actions": new_set,
                    "exit_uuid": str(uuid4()),
                    "destination": destination_uuid,
                }
                json_flow[Flow.ACTION_SETS].append(new_node)

            node_replacements[actionset["uuid"]].insert(0, new_node)  # so they're top to bottom
            destination = new_node

    def estimate_node_height(node):
        return (len(node["actions"]) * 75) + 75 if ("actions" in node) else 100

    for actionset_uuid, new_nodes in node_replacements.items():
        old_actionset = nodes_by_uuid[actionset_uuid]

        # if we're replacing a single actionset with multiple nodes, need to spread them out vertically
        if len(new_nodes) > 1:
            old_y = old_actionset.get("y", 0)
            old_height = len(old_actionset["actions"]) * 60
            extra_y = sum([estimate_node_height(n) for n in new_nodes]) - old_height

            # move rest of the flow down to make room
            move_nodes_down(json_flow, old_y + 1, extra_y)

            extra_y = estimate_node_height(new_nodes[0])
            for new_node in new_nodes[1:]:
                new_node["y"] += extra_y
                extra_y += estimate_node_height(new_node)

        # delete old actionset from flow
        json_flow[Flow.ACTION_SETS].remove(old_actionset)

    return json_flow


def migrate_to_version_11_6(json_flow, flow=None):
    """
    Versions before 11.6 maintained uuid mismatches on imported flows. This updates
    the flow definition with accurate group uuids
    """

    # this migration only matters for existing flows
    if not flow:
        return json_flow

    # only look up group once per flow migration
    uuid_map = {}

    def remap_group(group):
        if type(group) is dict:

            # we haven't been mapped yet (also, non-uuid groups can't be mapped)
            if "uuid" not in group or group["uuid"] not in uuid_map and group.get("name"):
                group_instance = ContactGroup.get_user_group_by_name(flow.org, group["name"])
                if group_instance:
                    # map group references that started with a uuid
                    if "uuid" in group:
                        uuid_map[group["uuid"]] = group_instance.uuid
                    group["uuid"] = group_instance.uuid

            # we were already mapped
            elif group["uuid"] in uuid_map:
                group["uuid"] = uuid_map[group["uuid"]]

    for actionset in json_flow.get(Flow.ACTION_SETS, []):
        for action in actionset[Flow.ACTIONS]:
            for group in action.get("groups", []):
                remap_group(group)

    for ruleset in json_flow.get(Flow.RULE_SETS, []):
        for rule in ruleset.get(Flow.RULES, []):
            if rule["test"]["type"] == InGroupTest.TYPE:
                group = rule["test"]["test"]
                remap_group(group)

    return json_flow


def migrate_to_version_11_5(json_flow, flow=None):
    """
    Replaces @flow.foo and @flow.foo.value with @extra.webhook where foo is a webhook or resthook ruleset
    """
    # figure out which rulesets are webhook or resthook calls
    rule_sets = json_flow.get("rule_sets", [])
    webhook_rulesets = set()
    non_webhook_rulesets = set()
    for r in rule_sets:
        slug = Flow.label_to_slug(r["label"])
        if not slug:  # pragma: no cover
            continue
        if r["ruleset_type"] in (RuleSet.TYPE_WEBHOOK, RuleSet.TYPE_RESTHOOK):
            webhook_rulesets.add(slug)
        else:
            non_webhook_rulesets.add(slug)

    # ignore any slugs of webhook rulesets which are also used by non-webhook rulesets
    slugs = webhook_rulesets.difference(non_webhook_rulesets)
    if not slugs:
        return json_flow

    # make a regex that matches a context reference to these (see https://regex101.com/r/65b2ZT/3)
    replace_pattern = r"flow\.(" + "|".join(slugs) + r")(\.value)?(?!\.\w)"
    replace_regex = regex.compile(replace_pattern, flags=regex.UNICODE | regex.IGNORECASE | regex.MULTILINE)
    replace_with = r"extra.\1"

    replace_templates(json_flow, lambda t: replace_regex.sub(replace_with, t))

    return json_flow


def migrate_to_version_11_4(json_flow, flow=None):
    """
    Replaces @flow.foo.text with @step.value for non-waiting rulesets, to bring old world functionality inline with the
    new engine, where @run.results.foo.input is always the router operand.
    """
    # figure out which rulesets aren't waits
    rule_sets = json_flow.get("rule_sets", [])
    non_waiting = {Flow.label_to_slug(r["label"]) for r in rule_sets if r["ruleset_type"] not in RuleSet.TYPE_WAIT}

    # make a regex that matches a context reference to the .text on any result from these
    replace_pattern = r"flow\.(" + "|".join(non_waiting) + r")\.text"
    replace_regex = regex.compile(replace_pattern, flags=regex.UNICODE | regex.IGNORECASE | regex.MULTILINE)
    replace_with = "step.value"

    # for every action in this flow, replace such references with @step.text
    for actionset in json_flow.get("action_sets", []):
        for action in actionset.get("actions", []):
            if action["type"] in ["reply", "send", "say", "email"]:
                msg = action["msg"]
                if isinstance(msg, str):
                    action["msg"] = replace_regex.sub(replace_with, msg)
                else:
                    for lang, text in msg.items():
                        msg[lang] = replace_regex.sub(replace_with, text)

    return json_flow


def migrate_to_version_11_3(json_flow, flow=None):
    """
    Migrates webhooks to support legacy format
    """
    for actionset in json_flow.get("action_sets", []):
        for action in actionset["actions"]:
            if action["type"] == "api" and action.get("action", "POST") == "POST":
                action["action"] = "POST"
                action["legacy_format"] = True

    for ruleset in json_flow.get("rule_sets", []):
        if ruleset["ruleset_type"] == "webhook":
            if ruleset["config"].get("webhook_action", "POST") == "POST":
                ruleset["config"]["legacy_format"] = True

    return json_flow


def _base_migrate_to_version_11_2(json_flow, country_code):
    if "base_language" in json_flow and json_flow["base_language"] != "base":
        iso_code = json_flow["base_language"]
        new_iso_code = iso6392_to_iso6393(iso_code, country_code)
        json_flow["base_language"] = new_iso_code

    return json_flow


def migrate_to_version_11_2(json_flow, flow=None):
    """
    Migrates base_language in flow definitions from iso639-2 to iso639-3
    """
    if flow is not None:
        country_code = flow.org.get_country_code()
    else:  # pragma: no cover
        raise ValueError("Languages depend on org, can not migrate to version 11 without org")

    return _base_migrate_to_version_11_2(json_flow, country_code=country_code)


def migrate_export_to_version_11_2(exported_json, org, same_site=True):
    """
        Migrates base_language in flow exports from iso639-2 to iso639-3
    """
    country_code = org.get_country_code()

    migrated_flows = []
    for sub_flow in exported_json.get("flows", []):
        flow = _base_migrate_to_version_11_2(sub_flow, country_code=country_code)
        migrated_flows.append(flow)

    exported_json["flows"] = migrated_flows

    return exported_json


def _base_migrate_to_version_11_1(json_flow, country_code):
    def _is_this_a_lang_object(obj):
        """
        Lang objects should only have keys of length == 3 or the string "base"
        """
        keys = set(obj.keys())

        for k in keys:
            if len(k) != 3 and k != "base":
                return False
        return True

    def _traverse(obj, country_code):
        if isinstance(obj, dict):

            if _is_this_a_lang_object(obj):
                new_obj = {}

                for key, val in obj.items():
                    if key == "base":
                        new_obj.update({key: val})
                    else:
                        new_key = iso6392_to_iso6393(key, country_code)
                        new_obj.update({new_key: val})

                value = new_obj
            elif "lang" in obj and obj["lang"] != "base":
                iso_code = obj["lang"]
                new_iso_code = iso6392_to_iso6393(iso_code, country_code)
                obj["lang"] = new_iso_code
                value = obj
            else:
                value = {k: _traverse(v, country_code) for k, v in obj.items()}

        elif isinstance(obj, list):
            value = [_traverse(elem, country_code) for elem in obj]
        else:
            value = obj

        return value

    return _traverse(json_flow, country_code=country_code)


def migrate_to_version_11_1(json_flow, flow=None):
    """
    Migrates translation language codes in flow definitions from iso639-2 to iso639-3
    """
    if flow is not None:
        country_code = flow.org.get_country_code()
    else:  # pragma: no cover
        raise ValueError("Languages depend on org, can not migrate to version 11 without org")

    return _base_migrate_to_version_11_1(json_flow, country_code=country_code)


def migrate_export_to_version_11_1(exported_json, org, same_site=True):
    """
        Migrates translation language codes in flow exports from iso639-2 to iso639-3
    """
    country_code = org.get_country_code()

    migrated_flows = []
    for sub_flow in exported_json.get("flows", []):
        flow = _base_migrate_to_version_11_1(sub_flow, country_code=country_code)
        migrated_flows.append(flow)

    exported_json["flows"] = migrated_flows

    return exported_json


def migrate_export_to_version_11_0(json_export, org, same_site=True):
    """
    Introduces the concept of format_location and format_date. This migration
    wraps all references to rulesets or contact fields which are locations or dates and
    wraps them appropriately
    """
    replacements = [
        [r"@date([^0-9a-zA-Z\.]|\.[^0-9a-zA-Z\.]|$|\.$)", r"@(format_date(date))\1"],
        [r"@date\.now", r"@(format_date(date.now))"],
    ]

    # get all contact fields that are date or location for this org
    fields = ContactField.user_fields.filter(org=org, is_active=True, value_type__in=["D", "S", "I", "W"]).only(
        "id", "value_type", "key"
    )

    for cf in fields:
        format_function = "format_date" if cf.value_type == "D" else "format_location"
        replacements.append(
            [
                r"@contact\.%s([^0-9a-zA-Z\.]|\.[^0-9a-zA-Z\.]|$|\.$)" % cf.key,
                r"@(%s(contact.%s))\1" % (format_function, cf.key),
            ]
        )

    for flow in json_export.get("flows", []):

        # figure out which rulesets are date or location
        for rs in flow.get("rule_sets", []):
            rs_type = None
            for rule in rs.get("rules", []):
                test = rule.get("test", {}).get("type")
                if not test:  # pragma: no cover
                    continue
                elif test == "true":
                    continue
                elif not rs_type:
                    rs_type = test
                elif rs_type and test != rs_type:
                    rs_type = "none"

            if rs["label"] is None:
                continue

            key = Flow.label_to_slug(rs["label"])

            # any reference to this result value's time property needs wrapped in format_date
            replacements.append([r"@flow\.%s\.time" % key, r"@(format_date(flow.%s.time))" % key])

            # how we wrap the actual result value depends on its type
            if rs_type in ["date", "date_before", "date_after", "date_equal"]:
                format_function = "format_date"
            elif rs_type in ["state", "district", "ward"]:
                format_function = "format_location"
            else:  # pragma: no cover
                continue

            replacements.append(
                [
                    r"@flow\.%s([^0-9a-zA-Z\.]|\.[^0-9a-zA-Z\.]|$|\.$)" % key,
                    r"@(%s(flow.%s))\1" % (format_function, key),
                ]
            )

        # for every action in this flow, look for replies, sends or says that use these fields and wrap them
        for actionset in flow.get("action_sets", []):
            for action in actionset.get("actions", []):
                if action["type"] in ["reply", "send", "say"]:
                    msg = action["msg"]
                    for lang, text in msg.items():
                        # some single message flows erroneously ended up with dicts inside dicts
                        if isinstance(text, dict):
                            text = next(iter(text.values()))

                        migrated_text = text
                        if isinstance(migrated_text, str):
                            for pattern, replacement in replacements:
                                migrated_text = regex.sub(
                                    pattern, replacement, migrated_text, flags=regex.UNICODE | regex.MULTILINE
                                )

                        msg[lang] = migrated_text

    return json_export


def migrate_to_version_11_0(json_flow, flow):
    return migrate_export_to_version_11_0({"flows": [json_flow]}, flow.org)["flows"][0]


def migrate_to_version_10_4(json_flow, flow=None):
    """
    Fixes flows which don't have exit_uuids on actionsets or uuids on actions
    """
    for actionset in json_flow["action_sets"]:
        if not actionset.get("exit_uuid"):
            actionset["exit_uuid"] = str(uuid4())

        for action in actionset["actions"]:
            uuid = action.get("uuid")
            if not uuid:
                action["uuid"] = str(uuid4())
    return json_flow


def migrate_to_version_10_3(json_flow, flow=None):
    """
    Adds exit_uuid to actionsets so flows can be migrated in goflow deterministically
    """
    for actionset in json_flow["action_sets"]:
        actionset["exit_uuid"] = str(uuid4())
    return json_flow


def migrate_to_version_10_2(json_flow, flow=None):
    """
    Fixes malformed single message flows that have a base language but a message action that isn't localized
    """
    # this is a case that can only arise from malformed revisions
    base_language = json_flow["base_language"]
    if not base_language:  # pragma: no cover
        base_language = "base"
    json_flow["base_language"] = base_language

    def update_action(action):
        if action["type"] == "reply":
            if not isinstance(action["msg"], dict):
                action["msg"] = {base_language: action["msg"]}
        return action

    return map_actions(json_flow, update_action)


def migrate_to_version_10_1(json_flow, flow):
    """
    Ensures all actions have uuids
    """
    json_flow = map_actions(json_flow, cleanse_group_names)
    for actionset in json_flow["action_sets"]:
        for action in actionset["actions"]:
            uuid = action.get("uuid", None)
            if not uuid:
                action["uuid"] = str(uuid4())
    return json_flow


def migrate_to_version_10(json_flow, flow):
    """
    Looks for webhook ruleset_types, adding success and failure cases and moving
    webhook_action and webhook to config
    """

    def replace_webhook_ruleset(ruleset, base_lang):
        # not a webhook? delete any turds of webhook or webhook_action
        if ruleset.get("ruleset_type", None) != "webhook":
            ruleset.pop("webhook_action", None)
            ruleset.pop("webhook", None)
            return ruleset

        if "config" not in ruleset:
            ruleset["config"] = dict()

        # webhook_action and webhook now live in config
        ruleset["config"]["webhook_action"] = ruleset["webhook_action"]
        del ruleset["webhook_action"]
        ruleset["config"]["webhook"] = ruleset["webhook"]
        del ruleset["webhook"]

        # we now can route differently on success and failure, route old flows to the same destination
        # for both
        destination = ruleset["rules"][0].get("destination", None)
        destination_type = ruleset["rules"][0].get("destination_type", None)
        old_rule_uuid = ruleset["rules"][0]["uuid"]

        rules = []
        for status in ["success", "failure"]:
            # maintain our rule uuid for the success case
            rule_uuid = old_rule_uuid if status == "success" else str(uuid4())
            new_rule = dict(
                test=dict(status=status, type="webhook_status"),
                category={base_lang: status.capitalize()},
                uuid=rule_uuid,
            )

            if destination:
                new_rule["destination"] = destination
                new_rule["destination_type"] = destination_type

            rules.append(new_rule)

        ruleset["rules"] = rules
        return ruleset

    # if we have rulesets, we need to fix those up with our new webhook types
    base_lang = json_flow.get("base_language", "base")
    json_flow = map_actions(json_flow, cleanse_group_names)
    if "rule_sets" in json_flow:
        rulesets = []
        for ruleset in json_flow["rule_sets"]:
            ruleset = replace_webhook_ruleset(ruleset, base_lang)
            rulesets.append(ruleset)

        json_flow["rule_sets"] = rulesets

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
    exported_string = replace(exported_string, r"@(extra\.flow)", "@parent")
    exported_string = replace(exported_string, r"(@\(.*?)extra\.flow(.*?\))", r"\1parent\2")

    # any references to @extra.contact are now @parent.contact
    exported_string = replace(exported_string, r"@(extra\.contact)", "@parent.contact")
    exported_string = replace(exported_string, r"(@\(.*?)extra\.contact(.*?\))", r"\1parent.contact\2")

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
            uuid = str(uuid4())
            id_map[obj_id] = uuid
        return uuid

    def replace_with_uuid(ele, manager, id_map, nested_name=None, obj=None, create_dict=False):
        # deal with case of having only a string and no name
        if isinstance(ele, str) and create_dict:
            # variable references should just stay put
            if len(ele) > 0 and ele[0] == "@":
                return ele
            else:
                ele = dict(name=ele)

        obj_id = ele.pop("id", None)
        obj_name = ele.pop("name", None)

        if same_site and not obj and obj_id:
            try:
                obj = manager.filter(pk=obj_id, org=org).first()
            except Exception:
                pass

        # nest it if we were given a nested name
        if nested_name:
            ele[nested_name] = dict()
            ele = ele[nested_name]

        if obj:
            ele["uuid"] = obj.uuid

            if obj.name:
                ele["name"] = obj.name
        else:
            if obj_id:
                ele["uuid"] = get_uuid(id_map, obj_id)

            if obj_name:
                ele["name"] = obj_name

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
            event = CampaignEvent.objects.filter(pk=ele["id"], campaign__org=org).first()
        replace_with_uuid(ele, CampaignEvent.objects, campaign_event_id_map, obj=event)

    def remap_contact(ele):
        from temba.contacts.models import Contact

        replace_with_uuid(ele, Contact.objects, contact_id_map)

    def remap_channel(ele):
        from temba.channels.models import Channel

        channel_id = ele.get("channel")
        if channel_id:  # pragma: needs cover
            channel = Channel.objects.filter(pk=channel_id).first()
            if channel:
                ele["channel"] = channel.uuid

    def remap_label(ele):
        from temba.msgs.models import Label

        replace_with_uuid(ele, Label.label_objects, label_id_map)

    for flow in exported_json.get("flows", []):
        flow = map_actions(flow, cleanse_group_names)

        for action_set in flow["action_sets"]:
            for action in action_set["actions"]:
                if action["type"] in ("add_group", "del_group", "send", "trigger-flow"):
                    groups = []
                    for group_json in action.get("groups", []):
                        groups.append(remap_group(group_json))
                    for contact_json in action.get("contacts", []):
                        remap_contact(contact_json)
                    if groups:
                        action["groups"] = groups
                if action["type"] in ("trigger-flow", "flow"):
                    remap_flow(action, "flow")
                if action["type"] == "add_label":
                    for label in action.get("labels", []):
                        remap_label(label)

        metadata = flow["metadata"]
        if "id" in metadata:
            if metadata.get("id", None):
                remap_flow(metadata)
            else:
                del metadata["id"]  # pragma: no cover

    for trigger in exported_json.get("triggers", []):
        if "flow" in trigger:
            remap_flow(trigger["flow"])
        for group in trigger["groups"]:  # pragma: no cover
            remap_group(group)
        remap_channel(trigger)

    for campaign in exported_json.get("campaigns", []):
        remap_campaign(campaign)
        remap_group(campaign["group"])
        for event in campaign.get("events", []):
            remap_campaign_event(event)
            if "id" in event["relative_to"]:
                del event["relative_to"]["id"]
            if "flow" in event:
                remap_flow(event["flow"])
    return exported_json


def migrate_to_version_9(json_flow, flow):
    """
    This version marks the first usage of subflow rulesets. Moves more items to UUIDs.
    """
    # inject metadata if it's missing
    from temba.flows.models import Flow

    if Flow.METADATA not in json_flow:
        json_flow[Flow.METADATA] = flow.get_legacy_metadata()
    return migrate_export_to_version_9(dict(flows=[json_flow]), flow.org)["flows"][0]


def migrate_to_version_8(json_flow, flow=None):
    """
    Migrates any expressions found in the flow definition to use the new @(...) syntax
    """

    def migrate_node(node):
        if isinstance(node, str):
            return migrate_v7_template(node)
        if isinstance(node, list):
            for n in range(len(node)):
                node[n] = migrate_node(node[n])
        if isinstance(node, dict):
            for key, val in node.items():
                node[key] = migrate_node(val)
        return node

    json_flow = map_actions(json_flow, cleanse_group_names)
    for rule_set in json_flow.get("rule_sets", []):
        for rule in rule_set["rules"]:
            migrate_node(rule["test"])

        if "operand" in rule_set and rule_set["operand"]:
            rule_set["operand"] = migrate_node(rule_set["operand"])
        if "webhook" in rule_set and rule_set["webhook"]:
            rule_set["webhook"] = migrate_node(rule_set["webhook"])

    for action_set in json_flow.get("action_sets", []):
        for action in action_set["actions"]:
            migrate_node(action)

    return json_flow


def migrate_to_version_7(json_flow, flow=None):
    """
    Adds flow details to metadata section
    """
    definition = json_flow.get("definition", None)

    # don't attempt if there isn't a nested definition block
    if definition:
        definition = map_actions(definition, cleanse_group_names)
        definition["flow_type"] = json_flow.get("flow_type", "F")
        metadata = definition.get("metadata", None)
        if not metadata:
            metadata = dict()
            definition["metadata"] = metadata

        metadata["name"] = json_flow.get("name")
        metadata["id"] = json_flow.get("id", None)
        metadata["uuid"] = json_flow.get("uuid", None)
        revision = json_flow.get("revision", None)
        if revision:
            metadata["revision"] = revision
        metadata["saved_on"] = json_flow.get("last_saved")

        # single message flows incorrectly created an empty rulesets
        # element which should be rule_sets instead
        if "rulesets" in definition:
            definition.pop("rulesets")
        return definition

    return json_flow  # pragma: needs cover


def migrate_to_version_6(json_flow, flow=None):
    """
    This migration removes the non-localized flow format. This means all potentially localizable
    text will be a dict from the outset. If no language is set, we will use 'base' as the
    default language.
    """

    definition = map_actions(json_flow.get("definition"), cleanse_group_names)

    # the name of the base language if its not set yet
    base_language = "base"

    def convert_to_dict(d, key):
        if key not in d:  # pragma: needs cover
            raise ValueError("Missing '%s' in dict: %s" % (key, d))

        if not isinstance(d[key], dict):
            d[key] = {base_language: d[key]}

    if "base_language" not in definition:
        definition["base_language"] = base_language

        for ruleset in definition.get("rule_sets", []):
            for rule in ruleset.get("rules"):

                # betweens haven't always required a category name, create one
                rule_test = rule["test"]
                if rule_test["type"] == "between" and "category" not in rule:
                    rule["category"] = "%s-%s" % (rule_test["min"], rule_test["max"])

                # convert the category name
                convert_to_dict(rule, "category")

                # convert our localized types
                if rule["test"]["type"] in [
                    ContainsTest.TYPE,
                    ContainsAnyTest.TYPE,
                    StartsWithTest.TYPE,
                    RegexTest.TYPE,
                ]:
                    convert_to_dict(rule["test"], "test")

        for actionset in definition.get("action_sets"):
            for action in actionset.get("actions"):
                if action["type"] in [SendAction.TYPE, ReplyAction.TYPE, SayAction.TYPE]:
                    convert_to_dict(action, "msg")
                if action["type"] == SayAction.TYPE:
                    if "recording" in action:
                        convert_to_dict(action, "recording")

    return json_flow


def migrate_to_version_5(json_flow, flow=None):
    """
    Adds passive rulesets. This necessitates injecting nodes in places where
    we were previously waiting implicitly with explicit waits.
    """

    def requires_step(operand):

        # if we start with =( then we are an expression
        is_expression = operand and len(operand) > 2 and operand[0:2] == "=("
        if "@step" in operand or (is_expression and "step" in operand):
            return True
        return False

    definition = map_actions(json_flow.get("definition"), cleanse_group_names)

    for ruleset in definition.get("rule_sets", []):

        response_type = ruleset.pop("response_type", None)
        ruleset_type = ruleset.get("ruleset_type", None)
        label = ruleset.get("label")

        # remove config from any rules, these are turds
        for rule in ruleset.get("rules"):
            if "config" in rule:
                del rule["config"]

        if response_type and not ruleset_type:

            # webhooks now live in their own ruleset, insert one
            webhook_url = ruleset.pop("webhook", None)
            webhook_action = ruleset.pop("webhook_action", None)

            has_old_webhook = webhook_url and ruleset_type != RuleSet.TYPE_WEBHOOK

            # determine our type from our operand
            operand = ruleset.get("operand")
            if not operand:
                operand = "@step.value"

            operand = operand.strip()

            # all previous ruleset that require step should be wait_message
            if requires_step(operand):
                # if we have an empty operand, go ahead and update it
                if not operand:  # pragma: needs cover
                    ruleset["operand"] = "@step.value"

                if response_type == "K":  # pragma: no cover
                    ruleset["ruleset_type"] = RuleSet.TYPE_WAIT_DIGITS
                elif response_type == "M":  # pragma: no cover
                    ruleset["ruleset_type"] = RuleSet.TYPE_WAIT_DIGIT
                elif response_type == "R":  # pragma: no cover
                    ruleset["ruleset_type"] = RuleSet.TYPE_WAIT_RECORDING
                else:

                    if operand == "@step.value":
                        ruleset["ruleset_type"] = RuleSet.TYPE_WAIT_MESSAGE
                    else:

                        ruleset["ruleset_type"] = RuleSet.TYPE_EXPRESSION

                        # if it's not a plain split, make us wait and create
                        # an expression split node to handle our response
                        pausing_ruleset = copy.deepcopy(ruleset)
                        pausing_ruleset["ruleset_type"] = RuleSet.TYPE_WAIT_MESSAGE
                        pausing_ruleset["operand"] = "@step.value"
                        pausing_ruleset["label"] = label + " Response"
                        remove_extra_rules(definition, pausing_ruleset)
                        insert_node(definition, pausing_ruleset, ruleset)

            else:
                # if there's no reference to step, figure out our type
                ruleset["ruleset_type"] = RuleSet.TYPE_EXPRESSION
                # special case contact and flow fields
                if " " not in operand and "|" not in operand:  # pragma: needs cover
                    if operand == "@contact.groups":
                        ruleset["ruleset_type"] = RuleSet.TYPE_EXPRESSION
                    elif operand.find("@contact.") == 0:
                        ruleset["ruleset_type"] = RuleSet.TYPE_CONTACT_FIELD
                    elif operand.find("@flow.") == 0:
                        ruleset["ruleset_type"] = RuleSet.TYPE_FLOW_FIELD

                # we used to stop at webhooks, now we need a new node
                # to make sure processing stops at this step now
                if has_old_webhook:
                    pausing_ruleset = copy.deepcopy(ruleset)
                    pausing_ruleset["ruleset_type"] = RuleSet.TYPE_WAIT_MESSAGE
                    pausing_ruleset["operand"] = "@step.value"
                    pausing_ruleset["label"] = label + " Response"
                    remove_extra_rules(definition, pausing_ruleset)
                    insert_node(definition, pausing_ruleset, ruleset)

            # finally insert our webhook node if necessary
            if has_old_webhook:
                webhook_ruleset = copy.deepcopy(ruleset)
                webhook_ruleset["webhook"] = webhook_url
                webhook_ruleset["webhook_action"] = webhook_action
                webhook_ruleset["operand"] = "@step.value"
                webhook_ruleset["ruleset_type"] = RuleSet.TYPE_WEBHOOK
                webhook_ruleset["label"] = label + " Webhook"
                remove_extra_rules(definition, webhook_ruleset)
                insert_node(definition, webhook_ruleset, ruleset)

    return json_flow


def cleanse_group_names(action):
    from temba.contacts.models import ContactGroup

    if action["type"] == "add_group" or action["type"] == "del_group":
        if "group" in action and "groups" not in action:
            action["groups"] = [action["group"]]
        for group in action["groups"]:
            if isinstance(group, dict):
                if "name" not in group:
                    group["name"] = "Unknown"
                if not ContactGroup.is_valid_name(group["name"]):
                    group["name"] = "%s %s" % ("Contacts", group["name"])
    return action


# ================================ Helper methods for flow migrations ===================================


def get_entry(json_flow):
    """
    Returns the entry node for the passed in flow, this is the ruleset or actionset with the lowest y
    """
    lowest_x = None
    lowest_y = None
    lowest_uuid = None

    for ruleset in json_flow.get("rule_sets", []):
        if lowest_y is None or ruleset["y"] < lowest_y:
            lowest_uuid = ruleset["uuid"]
            lowest_y = ruleset["y"]
            lowest_x = ruleset["x"]
        elif lowest_y == ruleset["y"]:
            if ruleset["x"] < lowest_x:
                lowest_uuid = ruleset["uuid"]
                lowest_y = ruleset["y"]
                lowest_x = ruleset["x"]

    for actionset in json_flow.get("action_sets", []):
        if lowest_y is None or actionset["y"] < lowest_y:
            lowest_uuid = actionset["uuid"]
            lowest_y = actionset["y"]
            lowest_x = actionset["x"]
        elif lowest_y == actionset["y"]:
            if actionset["x"] < lowest_x:
                lowest_uuid = actionset["uuid"]
                lowest_y = actionset["y"]
                lowest_x = actionset["x"]
    return lowest_uuid


def map_actions(json_flow, fixer_method):
    """
    Given a JSON flow, runs fixer_method on every action. If fixer_method returns None, the action is
    removed, otherwise the returned action is used.
    """
    action_sets = []
    original_action_sets = json_flow.get("action_sets", [])
    for actionset in original_action_sets:
        actions = []
        for action in actionset.get("actions", []):
            fixed_action = fixer_method(action)
            if fixed_action is not None:
                actions.append(fixed_action)

        actionset["actions"] = actions

        # only add in this actionset if there are actions in it
        if actions:
            action_sets.append(actionset)

    json_flow["action_sets"] = action_sets

    # if we trimmed off an actionset, reevaluate our start node
    if len(action_sets) < len(original_action_sets):
        json_flow["entry"] = get_entry(json_flow)

    return json_flow


def remove_extra_rules(json_flow, ruleset):
    """ Remove all rules but the all responses rule """
    rules = []
    old_rules = ruleset.get("rules")
    for rule in old_rules:
        if rule["test"]["type"] == "true":
            if "base_language" in json_flow:
                rule["category"][json_flow["base_language"]] = "All Responses"
            else:
                rule["category"] = "All Responses"
            rules.append(rule)

    ruleset["rules"] = rules


def insert_node(flow, node, _next):
    """ Inserts a node right before _next """

    def update_destination(node_to_update, uuid):
        if node_to_update.get("actions", []):  # pragma: needs cover
            node_to_update["destination"] = uuid
        else:
            for rule in node_to_update.get("rules", []):
                rule["destination"] = uuid

    # make sure we have a fresh uuid
    node["uuid"] = _next["uuid"]
    _next["uuid"] = str(uuid4())
    update_destination(node, _next["uuid"])

    # bump everybody down
    move_nodes_down(flow, node.get("y"))

    # we are an actionset
    if node.get("actions", []):  # pragma: needs cover
        node.destination = _next["uuid"]
        flow["action_sets"].append(node)

    # otherwise point all rules to the same place
    else:
        for rule in node.get("rules", []):
            rule["destination"] = _next["uuid"]
        flow["rule_sets"].append(node)


def move_nodes_down(flow, below, delta=100):
    """
    Move any node below the given Y value down by delta
    """

    # bump everybody down
    for actionset in flow.get("action_sets", []):
        if actionset.get("y") >= below:
            actionset["y"] += delta

    for ruleset in flow.get("rule_sets", []):
        if ruleset.get("y") >= below:
            ruleset["y"] += delta


def replace_templates(json_flow, replace_func):
    """
    Applies a replace function to all the template fields in a flow definition
    """
    for actionset in json_flow.get("action_sets", []):
        for action in actionset.get("actions", []):
            if action["type"] in ["reply", "send", "say", "email"]:
                msg = action["msg"]
                if isinstance(msg, str):
                    action["msg"] = replace_func(msg)  # pragma: no cover
                else:
                    for lang, text in msg.items():
                        msg[lang] = replace_func(text)
            elif action["type"] == "save":
                action["value"] = replace_func(action["value"])
            elif action["type"] == "api":
                action["webhook"] = replace_func(action["webhook"])

    for ruleset in json_flow.get("rule_sets", []):
        if "operand" in ruleset:
            operand = ruleset["operand"]
            ruleset["operand"] = replace_func(operand)

            # if we've changed the operand on a flow_field ruleset.. it has to become a split by expression
            if operand != ruleset["operand"] and ruleset["ruleset_type"] == "flow_field":
                ruleset["ruleset_type"] = "expression"

            for rule in ruleset.get("rules", []):
                test = rule["test"]
                if "test" in test and isinstance(test["test"], dict):
                    for lang, test_text in test["test"].items():
                        test["test"][lang] = replace_func(test_text)

        if "config" in ruleset:
            config = ruleset["config"]
            if "webhook" in config:
                config["webhook"] = replace_func(config["webhook"])
