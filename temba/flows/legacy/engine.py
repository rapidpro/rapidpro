# This file will be removed at some point and only still exists to enable some unit tests which have still yet to be
# rewritten without use of the legacy flow engine. None of this code is run in production and is thus excluded from
# test coverage checks.

import numbers
from collections import OrderedDict
from datetime import datetime, timedelta
from uuid import uuid4

import regex

from django.conf import settings
from django.db.models import Q, QuerySet
from django.utils import timezone

from temba.contacts.models import Contact, ContactGroup
from temba.locations.models import AdminBoundary
from temba.msgs.models import SENT, Msg
from temba.orgs.models import Language
from temba.values.constants import Value

from .definition import ReplyAction, get_node
from .expressions import contact_context, evaluate, flow_context, run_context

INVALID_EXTRA_KEY_CHARS = regex.compile(r"[^a-zA-Z0-9_]")


def flow_start_start(start):
    from temba.flows.models import FlowStart

    groups = list(start.groups.all())
    contacts = list(start.contacts.all())

    # load up our extra if any
    extra = start.extra if start.extra else None

    flow_start(
        start.flow,
        groups,
        contacts,
        start=start,
        extra=extra,
        restart_participants=start.restart_participants,
        include_active=start.include_active,
    )

    start.status = FlowStart.STATUS_COMPLETE
    start.save(update_fields=("status",))


def flow_start(
    flow,
    groups,
    contacts,
    restart_participants=False,
    start_msg=None,
    extra=None,
    start=None,
    interrupt=True,
    include_active=True,
):
    """
    Starts a flow for the passed in groups and contacts.
    """

    from temba.msgs.models import FLOW
    from temba.flows.models import FlowRun

    if not getattr(settings, "USES_LEGACY_ENGINE", False):
        raise ValueError("Use of legacy engine when USES_LEGACY_ENGINE not set")

    # build up querysets of our groups for memory efficiency
    if isinstance(groups, QuerySet):
        group_qs = groups
    else:
        group_qs = ContactGroup.all_groups.filter(id__in=[g.id for g in groups])

    # build up querysets of our contacts for memory efficiency
    if isinstance(contacts, QuerySet):
        contact_qs = contacts
    else:
        contact_qs = Contact.objects.filter(id__in=[c.id for c in contacts])

    flow.ensure_current_version()

    if not flow.entry_uuid:
        return []

    if start_msg and start_msg.id:
        start_msg.msg_type = FLOW
        start_msg.save(update_fields=["msg_type"])

    all_contact_ids = list(
        Contact.objects.filter(Q(all_groups__in=group_qs) | Q(id__in=contact_qs))
        .order_by("id")
        .values_list("id", flat=True)
        .distinct("id")
    )

    if not restart_participants:
        # exclude anybody who has already participated in the flow
        already_started = set(flow.runs.all().values_list("contact_id", flat=True))
        all_contact_ids = [contact_id for contact_id in all_contact_ids if contact_id not in already_started]

    if not include_active:
        # exclude anybody who has an active flow run
        already_active = set(FlowRun.objects.filter(is_active=True, org=flow.org).values_list("contact_id", flat=True))
        all_contact_ids = [contact_id for contact_id in all_contact_ids if contact_id not in already_active]

    # for the contacts that will be started, exit any existing flow runs except system flow runs
    active_runs = FlowRun.objects.filter(is_active=True, contact__pk__in=all_contact_ids).exclude(flow__is_system=True)
    bulk_exit(active_runs, FlowRun.EXIT_TYPE_INTERRUPTED)

    if not all_contact_ids:
        return []

    return _flow_start(flow, all_contact_ids, start_msg=start_msg, extra=extra, flow_start=start)


def _flow_start(flow, contact_ids, start_msg=None, extra=None, flow_start=None, parent_run=None):
    from temba.msgs.models import OUTGOING, Msg
    from temba.flows.models import Flow, FlowRun, ActionSet, RuleSet

    if parent_run:
        parent_context = run_context(parent_run, contact_ctx=str(parent_run.contact.uuid))
    else:
        parent_context = None

    contacts = Contact.objects.filter(id__in=contact_ids)
    Contact.bulk_cache_initialize(flow.org, contacts)
    contact_map = {c.id: c for c in contacts}

    # these fields are the initial state for our flow run
    run_fields = {}  # this should be the default value of the FlowRun.fields
    if extra:
        # we keep more values in @extra for new flow runs because we might be passing the state
        (normalized_fields, count) = _normalize_fields(extra, 256 * 4)
        run_fields = normalized_fields

    # create all our flow runs for this set of contacts at once
    batch = []
    now = timezone.now()

    for contact_id in contact_ids:
        contact = contact_map[contact_id]
        run = FlowRun.create(
            flow,
            contact,
            fields=run_fields,
            start=flow_start,
            created_on=now,
            parent=parent_run,
            parent_context=parent_context,
            db_insert=False,
            responded=start_msg is not None,
        )
        batch.append(run)

    runs = FlowRun.objects.bulk_create(batch)

    # build a map of contact to flow run
    run_map = dict()
    for run in runs:
        run.flow = flow
        run.org = flow.org

        run_map[run.contact_id] = run

    # update our expiration date on our runs, we do this by calculating it on one run then updating all others
    run.update_expiration(timezone.now())

    # if we have more than one run, update the others to the same expiration
    if len(run_map) > 1:
        FlowRun.objects.filter(id__in=[r.id for r in runs]).update(
            expires_on=run.expires_on, modified_on=timezone.now()
        )

    # now execute our actual flow steps
    (entry_actions, entry_rules) = (None, None)
    if flow.entry_type == Flow.NODE_TYPE_ACTIONSET:
        entry_actions = ActionSet.objects.filter(uuid=flow.entry_uuid).first()
        if entry_actions:
            entry_actions.flow = flow

    elif flow.entry_type == Flow.NODE_TYPE_RULESET:
        entry_rules = RuleSet.objects.filter(flow=flow, uuid=flow.entry_uuid).first()
        if entry_rules:
            entry_rules.flow = flow

    for run in runs:
        contact = run.contact
        run_msgs = [start_msg] if start_msg else []
        arrived_on = timezone.now()

        if entry_actions:
            run_msgs += _execute_actions(entry_actions, run, start_msg)

            _add_step(run, entry_actions, run_msgs, arrived_on=arrived_on)

            # and onto the destination
            if entry_actions.destination:
                destination = get_node(entry_actions.flow, entry_actions.destination, entry_actions.destination_type)

                _add_step(run, destination, exit_uuid=entry_actions.exit_uuid)

                msg = Msg(org=flow.org, contact=contact, text="", id=0)
                handled, step_msgs = _handle_destination(destination, run, msg)
                run_msgs += step_msgs

            else:
                _set_run_completed(run, exit_uuid=None)

        elif entry_rules:
            _add_step(run, entry_rules, run_msgs, arrived_on=arrived_on)

            # if we have a start message, go and handle the rule
            if start_msg:
                find_and_handle(start_msg, triggered_start=True)

            # if we didn't get an incoming message, see if we need to evaluate it passively
            elif entry_rules.ruleset_type not in RuleSet.TYPE_WAIT:
                # create an empty placeholder message
                msg = Msg(org=flow.org, contact=contact, text="", id=0)
                handled, step_msgs = _handle_destination(entry_rules, run, msg)
                run_msgs += step_msgs

        # set the msgs that were sent by this run so that any caller can deal with them
        run.start_msgs = [m for m in run_msgs if m.direction == OUTGOING]

    return runs


def _add_step(run, node, msgs=(), exit_uuid=None, arrived_on=None):
    """
    Adds a new step to the given run
    """

    from temba.flows.models import FlowRun

    if not arrived_on:
        arrived_on = timezone.now()

    # complete previous step
    if run.path and exit_uuid:
        run.path[-1][FlowRun.PATH_EXIT_UUID] = exit_uuid

    # create new step
    run.path.append(
        {
            FlowRun.PATH_STEP_UUID: str(uuid4()),
            FlowRun.PATH_NODE_UUID: node.uuid,
            FlowRun.PATH_ARRIVED_ON: arrived_on.isoformat(),
        }
    )

    update_fields = ["path", "current_node_uuid"]

    if msgs:
        _add_messages(run, msgs, do_save=False)
        update_fields += ["responded", "events"]

    run.current_node_uuid = run.path[-1][FlowRun.PATH_NODE_UUID]
    run.save(update_fields=update_fields)


def find_and_handle(msg, triggered_start=False, user_input=True):
    from temba.flows.models import Flow, FlowRun

    for run in _get_active_runs_for_contact(msg.contact):
        flow = run.flow
        flow.ensure_current_version()

        # it's possible Flow.start is in the process of creating a run for this contact, in which case
        # record this message has handled so it doesn't start any new flows
        if not run.path:
            if run.created_on > timezone.now() - timedelta(minutes=10):
                return True, []
            else:
                return False, []
        last_step = run.path[-1]
        destination = get_node(flow, last_step[FlowRun.PATH_NODE_UUID], Flow.NODE_TYPE_RULESET)

        # this node doesn't exist anymore, mark it as left so they leave the flow
        if not destination:
            _set_run_completed(run, exit_uuid=None)
            return True, []

        (handled, msgs) = _handle_destination(
            destination, run, msg, user_input=user_input, triggered_start=triggered_start
        )

        if handled:
            return True, msgs

    return False, []


def _handle_destination(destination, run, msg, user_input=False, triggered_start=False):
    from temba.flows.models import ActionSet, RuleSet

    path = []
    msgs = []

    # lookup our next destination
    handled = False

    while destination:
        result = {"handled": False}

        if isinstance(destination, RuleSet):
            should_pause = False

            # check if we need to stop
            if destination.ruleset_type in RuleSet.TYPE_WAIT:
                should_pause = True

            if user_input or not should_pause:
                result = _handle_ruleset(destination, run, msg)
                path.append(destination.uuid)

                # add any messages generated by this ruleset
                msgs += result.get("msgs", [])

            # if we used this input, then mark our user input as used
            if should_pause:
                user_input = False

                # once we handle user input, reset our path
                path = []

        elif isinstance(destination, ActionSet):
            result = _handle_actionset(destination, run, msg)
            path.append(destination.uuid)

            # add any generated messages to be sent at once
            msgs += result.get("msgs", [])

        # if this is a triggered start, we only consider user input on the first step, so clear it now
        if triggered_start:
            user_input = False

        # lookup our next destination
        destination = result.get("destination", None)

        # if any one of our destinations handled us, consider it handled
        if result.get("handled", False):
            handled = True

    return handled, msgs


def _handle_actionset(actionset, run, msg):
    # not found, escape out, but we still handled this message, user is now out of the flow
    if not actionset:
        _set_run_completed(run, exit_uuid=None)
        return dict(handled=True, destination=None, destination_type=None)

    # actually execute all the actions in our actionset
    msgs = _execute_actions(actionset, run, msg)
    _add_messages(run, [m for m in msgs if not getattr(m, "from_other_run", False)])

    # and onto the destination
    destination = get_node(actionset.flow, actionset.destination, actionset.destination_type)
    if destination:
        _add_step(run, destination, exit_uuid=actionset.exit_uuid)
    else:
        _set_run_completed(run, exit_uuid=actionset.exit_uuid)

    return dict(handled=True, destination=destination, msgs=msgs)


def _execute_actions(actionset, run, msg):
    actions = actionset.get_actions()
    msgs = []

    run.contact.org = run.org
    context = flow_context(run.flow, run.contact, msg, run=run)

    for a, action in enumerate(actions):
        if isinstance(action, ReplyAction):
            from temba.flows.models import get_flow_user

            user = get_flow_user(run.org)
            text = get_localized_text(run.flow, action.msg, run.contact)

            msg = Msg.create_outgoing(
                run.org,
                user,
                run.contact,
                text,
                response_to=msg if msg and msg.id else None,
                expressions_context=context,
                msg_type=action.MSG_TYPE,
                status=SENT,
                sent_on=timezone.now(),
            )
            msgs.append(msg)

        # if there are more actions, rebuild the parts of the context that may have changed
        if a < len(actions) - 1:
            context["contact"] = contact_context(run.contact)
            context["extra"] = run.fields

    return msgs


def _handle_ruleset(ruleset, run, msg_in):
    from temba.flows.models import RuleSet

    msgs_out = []

    # find a matching rule
    result_rule, result_value, result_input = _find_matching_rule(ruleset, run, msg_in)

    flow = ruleset.flow

    # add the message to our step
    if msg_in.id:
        _add_messages(run, [msg_in])
        run.update_expiration(timezone.now())

    if ruleset.ruleset_type in RuleSet.TYPE_MEDIA and msg_in.attachments:
        # store the media path as the value
        result_value = msg_in.attachments[0].split(":", 1)[1]

    _save_ruleset_result(ruleset, run, result_rule, result_value, result_input, org=flow.org)

    # no destination for our rule?  we are done, though we did handle this message, user is now out of the flow
    if not result_rule.destination:
        _set_run_completed(run, exit_uuid=result_rule.uuid)
        return dict(handled=True, destination=None, destination_type=None, msgs=msgs_out)

    # Create the step for our destination
    destination = get_node(flow, result_rule.destination, result_rule.destination_type)
    if destination:
        _add_step(run, destination, exit_uuid=result_rule.uuid)

    return dict(handled=True, destination=destination, msgs=msgs_out)


def _add_messages(run, msgs, do_save=True):
    """
    Associates the given messages with a run
    """
    from temba.msgs.models import FLOW, INBOX, INCOMING
    from temba.flows.models import FlowRun, Events

    if run.events is None:
        run.events = []

    # find the path step these messages belong to
    path_step = run.path[-1]

    existing_msg_uuids = set()
    for e in run.get_msg_events():
        msg_uuid = e["msg"].get("uuid")
        if msg_uuid:
            existing_msg_uuids.add(msg_uuid)

    needs_update = False

    def serialize_message(msg):
        serialized = {"uuid": str(msg.uuid), "text": msg.text}

        if msg.contact_urn_id:
            serialized["urn"] = msg.contact_urn.urn
        if msg.channel_id:
            serialized["channel"] = {"uuid": str(msg.channel.uuid), "name": msg.channel.name or ""}
        if msg.attachments:
            serialized["attachments"] = msg.attachments

        return serialized

    for msg in msgs:
        # or messages which have already been attached to this run
        if str(msg.uuid) in existing_msg_uuids:
            continue

        run.events.append(
            {
                FlowRun.EVENT_TYPE: Events.msg_received.name if msg.direction == INCOMING else Events.msg_created.name,
                FlowRun.EVENT_CREATED_ON: msg.created_on.isoformat(),
                FlowRun.EVENT_STEP_UUID: path_step.get(FlowRun.PATH_STEP_UUID),
                "msg": serialize_message(msg),
            }
        )

        existing_msg_uuids.add(str(msg.uuid))
        needs_update = True

        # incoming non-IVR messages won't have a type yet so update that
        if not msg.msg_type or msg.msg_type == INBOX:
            msg.msg_type = FLOW
            msg.save(update_fields=["msg_type"])

        # if message is from contact, mark run as responded
        if msg.direction == INCOMING:
            if not run.responded:
                run.responded = True

    if needs_update and do_save:
        run.save(update_fields=("responded", "events"))


def bulk_exit(runs, exit_type):
    """
    Exits (expires, interrupts) runs in bulk
    """

    from temba.flows.models import FlowRun, FlowSession

    now = timezone.now()

    run_ids = list(runs[:5000].values_list("id", flat=True))
    runs = FlowRun.objects.filter(id__in=run_ids)
    runs.update(
        is_active=False, exited_on=now, exit_type=exit_type, modified_on=now, child_context=None, parent_context=None
    )

    # mark session as completed if this is an interruption
    if exit_type == FlowRun.EXIT_TYPE_INTERRUPTED:
        (
            FlowSession.objects.filter(id__in=runs.exclude(session=None).values_list("session_id", flat=True))
            .filter(status=FlowSession.STATUS_WAITING)
            .update(status=FlowSession.STATUS_INTERRUPTED, ended_on=now)
        )


def _find_matching_rule(ruleset, run, msg):
    orig_text = None
    if msg:
        orig_text = msg.text

    msg.contact = run.contact
    context = flow_context(run.flow, run.contact, msg, run=run)

    # if we have a custom operand, figure that out
    operand = None
    if ruleset.operand:
        (operand, errors) = evaluate(ruleset.operand, context, org=run.flow.org)
    elif msg:
        operand = str(msg)

    try:
        rules = ruleset.get_rules()
        for rule in rules:
            (result, value) = rule.matches(run, msg, context, operand)
            if result:
                # treat category as the base category
                return rule, value, operand
    finally:
        if msg:
            msg.text = orig_text

    return None, None, None


def _save_ruleset_result(ruleset, run, rule, raw_value, raw_input, org=None):
    org = org or ruleset.flow.org
    contact_language = run.contact.language if run.contact.language in org.get_language_codes() else None

    _save_run_result(
        run,
        name=ruleset.label,
        node_uuid=ruleset.uuid,
        category=rule.get_category_name(run.flow.base_language),
        category_localized=rule.get_category_name(run.flow.base_language, contact_language),
        raw_value=raw_value,
        raw_input=raw_input,
    )


def _save_run_result(run, name, node_uuid, category, category_localized, raw_value, raw_input):
    from temba.flows.models import Flow, FlowRun

    # slug our name
    key = Flow.label_to_slug(name)

    # create our result dict
    results = run.results
    results[key] = {
        FlowRun.RESULT_NAME: name,
        FlowRun.RESULT_NODE_UUID: node_uuid,
        FlowRun.RESULT_CATEGORY: category,
        FlowRun.RESULT_VALUE: _serialize_result_value(raw_value),
        FlowRun.RESULT_CREATED_ON: timezone.now().isoformat(),
    }

    if raw_input is not None:
        results[key][FlowRun.RESULT_INPUT] = str(raw_input)

    # if we have a different localized name for our category, save it as well
    if category != category_localized:
        results[key][FlowRun.RESULT_CATEGORY_LOCALIZED] = category_localized

    run.results = results
    run.modified_on = timezone.now()
    run.save(update_fields=["results", "modified_on"])


def _serialize_result_value(value):
    """
    Utility method to give the serialized value for the passed in value
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.isoformat()
    elif isinstance(value, AdminBoundary):
        return value.path
    else:
        return str(value)


def _set_run_completed(run, *, exit_uuid, completed_on=None):
    """
    Mark a run as complete
    """
    now = timezone.now()

    if not completed_on:
        completed_on = now

    # mark this run as inactive
    if exit_uuid:
        run.path[-1]["exit_uuid"] = str(exit_uuid)
    run.exit_type = run.EXIT_TYPE_COMPLETED
    run.exited_on = completed_on
    run.is_active = False
    run.parent_context = None
    run.child_context = None
    run.save(
        update_fields=("path", "exit_type", "exited_on", "modified_on", "is_active", "parent_context", "child_context")
    )


def _set_run_interrupted(run):
    """
    Mark run as interrupted
    """
    now = timezone.now()

    # mark this flow as inactive
    run.exit_type = run.EXIT_TYPE_INTERRUPTED
    run.exited_on = now
    run.is_active = False
    run.parent_context = None
    run.child_context = None
    run.save(update_fields=("exit_type", "exited_on", "modified_on", "is_active", "parent_context", "child_context"))


def _update_run_fields(run, field_map, do_save=True):
    # validate our field
    (field_map, count) = _normalize_fields(field_map)
    if not run.fields:
        run.fields = field_map
    else:
        existing_map = run.fields
        existing_map.update(field_map)
        run.fields = existing_map

    if do_save:
        run.save(update_fields=["fields"])


def _normalize_fields(fields, max_values=None, count=-1):
    """
    Turns an arbitrary dictionary into a dictionary containing only string keys and values
    """

    def normalize_key(key):
        return INVALID_EXTRA_KEY_CHARS.sub("_", key)[:255]

    if max_values is None:
        max_values = 256

    if isinstance(fields, str):
        return fields[: Value.MAX_VALUE_LEN], count + 1

    elif isinstance(fields, numbers.Number) or isinstance(fields, bool):
        return fields, count + 1

    elif isinstance(fields, dict):
        count += 1
        field_dict = OrderedDict()
        for (k, v) in fields.items():
            (field_dict[normalize_key(k)], count) = _normalize_fields(v, max_values, count)

            if count >= max_values:
                break

        return field_dict, count

    elif isinstance(fields, list):
        count += 1
        list_dict = OrderedDict()
        for (i, v) in enumerate(fields):
            (list_dict[str(i)], count) = _normalize_fields(v, max_values, count)

            if count >= max_values:  # pragma: needs cover
                break

        return list_dict, count

    elif fields is None:
        return "", count + 1
    else:
        raise ValueError("Unsupported type %s in extra" % str(type(fields)))


def _get_active_runs_for_contact(contact):
    from temba.flows.models import Flow, FlowRun

    runs = FlowRun.objects.filter(is_active=True, flow__is_active=True, contact=contact)

    # don't consider voice runs, those are interactive
    runs = runs.exclude(flow__flow_type=Flow.TYPE_VOICE)

    # real contacts don't deal with archived flows
    runs = runs.filter(flow__is_archived=False)

    return runs.select_related("flow", "contact", "flow__org", "connection").order_by("-id")


def get_localized_text(flow, text_translations, contact=None):
    org_languages = flow.org.get_language_codes()
    preferred_languages = []

    if contact and contact.language and contact.language in org_languages:
        preferred_languages.append(contact.language)

    if flow.org.primary_language:
        preferred_languages.append(flow.org.primary_language.iso_code)

    preferred_languages.append(flow.base_language)

    return Language.get_localized_text(text_translations, preferred_languages)
