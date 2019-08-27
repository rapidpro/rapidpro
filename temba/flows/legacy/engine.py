import logging
import time
from datetime import timedelta
from uuid import uuid4

import requests

from django.conf import settings
from django.db.models import Q, QuerySet
from django.utils import timezone

from temba.contacts.models import Contact, ContactGroup
from temba.msgs.models import DELIVERED, FAILED, FLOW, OUTGOING, PENDING, Msg
from temba.utils import json, prepped_request_to_str
from temba.utils.http import http_headers

logger = logging.getLogger(__name__)


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


def flow_start(  # pragma: no cover
    flow,
    groups,
    contacts,
    restart_participants=False,
    started_flows=None,
    start_msg=None,
    extra=None,
    start=None,
    parent_run=None,
    interrupt=True,
    include_active=True,
):
    """
    Starts a flow for the passed in groups and contacts.
    """

    from temba.flows.models import Flow, FlowRun

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

    if started_flows is None:
        started_flows = []

    # prevents infinite loops
    if flow.pk in started_flows:
        return []

    # add this flow to our list of started flows
    started_flows.append(flow.pk)

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

    # if we have a parent run, find any parents/grandparents that are active, we'll keep these active
    ancestor_ids = []
    ancestor = parent_run
    while ancestor:
        # we don't consider it an ancestor if it's not current in our start list
        if ancestor.contact.id not in all_contact_ids:
            break
        ancestor_ids.append(ancestor.id)
        ancestor = ancestor.parent

    # for the contacts that will be started, exit any existing flow runs except system flow runs
    active_runs = (
        FlowRun.objects.filter(is_active=True, contact__pk__in=all_contact_ids)
        .exclude(id__in=ancestor_ids)
        .exclude(flow__is_system=True)
    )
    FlowRun.bulk_exit(active_runs, FlowRun.EXIT_TYPE_INTERRUPTED)

    # if we are interrupting parent flow runs, mark them as completed
    if ancestor_ids and interrupt:
        ancestor_runs = FlowRun.objects.filter(id__in=ancestor_ids)
        FlowRun.bulk_exit(ancestor_runs, FlowRun.EXIT_TYPE_COMPLETED)

    if not all_contact_ids:
        return []

    if flow.flow_type == Flow.TYPE_VOICE:
        raise ValueError("IVR flow '%s' no longer supported" % flow.name)

    return _flow_start(
        flow,
        all_contact_ids,
        started_flows=started_flows,
        start_msg=start_msg,
        extra=extra,
        flow_start=start,
        parent_run=parent_run,
    )


def _flow_start(flow, contact_ids, started_flows=None, start_msg=None, extra=None, flow_start=None, parent_run=None):
    from temba.flows.models import Flow, FlowRun, ActionSet, RuleSet

    if parent_run:
        parent_context = parent_run.build_expressions_context(contact_context=str(parent_run.contact.uuid))
    else:
        parent_context = None

    contacts = Contact.objects.filter(id__in=contact_ids)
    Contact.bulk_cache_initialize(flow.org, contacts)
    contact_map = {c.id: c for c in contacts}

    # these fields are the initial state for our flow run
    run_fields = {}  # this should be the default value of the FlowRun.fields
    if extra:
        # we keep more values in @extra for new flow runs because we might be passing the state
        (normalized_fields, count) = FlowRun.normalize_fields(extra, settings.FLOWRUN_FIELDS_SIZE * 4)
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

    msgs_to_send = []

    for run in runs:
        contact = run.contact

        # each contact maintains its own list of started flows
        started_flows_by_contact = list(started_flows)
        run_msgs = [start_msg] if start_msg else []
        arrived_on = timezone.now()

        try:
            if entry_actions:
                run_msgs += entry_actions.execute_actions(run, start_msg, started_flows_by_contact)

                add_step(run, entry_actions, run_msgs, arrived_on=arrived_on)

                # and onto the destination
                if entry_actions.destination:
                    destination = Flow.get_node(
                        entry_actions.flow, entry_actions.destination, entry_actions.destination_type
                    )

                    add_step(run, destination, exit_uuid=entry_actions.exit_uuid)

                    msg = Msg(org=flow.org, contact=contact, text="", id=0)
                    handled, step_msgs = _handle_destination(
                        destination, run, msg, started_flows_by_contact, trigger_send=False, continue_parent=False
                    )
                    run_msgs += step_msgs

                else:
                    run.set_completed(exit_uuid=None)

            elif entry_rules:
                add_step(run, entry_rules, run_msgs, arrived_on=arrived_on)

                # if we have a start message, go and handle the rule
                if start_msg:
                    find_and_handle(start_msg, started_flows_by_contact, triggered_start=True)

                # if we didn't get an incoming message, see if we need to evaluate it passively
                elif not entry_rules.is_pause():
                    # create an empty placeholder message
                    msg = Msg(org=flow.org, contact=contact, text="", id=0)
                    handled, step_msgs = _handle_destination(
                        entry_rules, run, msg, started_flows_by_contact, trigger_send=False, continue_parent=False
                    )
                    run_msgs += step_msgs

            # set the msgs that were sent by this run so that any caller can deal with them
            run.start_msgs = [m for m in run_msgs if m.direction == OUTGOING]

            # add these messages as ones that are ready to send
            for msg in run_msgs:
                if msg.direction == OUTGOING:
                    msgs_to_send.append(msg)

        except Exception:
            logger.error(
                "Failed starting flow %d for contact %d" % (flow.id, contact.id), exc_info=1, extra={"stack": True}
            )

            # mark this flow as interrupted
            run.set_interrupted()

            # mark our messages as failed
            Msg.objects.filter(id__in=[m.id for m in run_msgs if m.direction == OUTGOING]).update(status=FAILED)

            # remove our msgs from our parent's concerns
            run.start_msgs = []

    # trigger our messages to be sent
    if msgs_to_send and not parent_run:
        # then send them off
        msgs_to_send.sort(key=lambda message: (message.contact_id, message.created_on))
        Msg.objects.filter(id__in=[m.id for m in msgs_to_send]).update(status=PENDING)

        # trigger a sync
        flow.org.trigger_send(msgs_to_send)

    return runs


def add_step(run, node, msgs=(), exit_uuid=None, arrived_on=None):
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

    # trim path to ensure it can't grow indefinitely
    if len(run.path) > FlowRun.PATH_MAX_STEPS:
        run.path = run.path[len(run.path) - FlowRun.PATH_MAX_STEPS :]

    update_fields = ["path", "current_node_uuid"]

    if msgs:
        run.add_messages(msgs, do_save=False)
        update_fields += ["responded", "events"]

    run.current_node_uuid = run.path[-1][FlowRun.PATH_NODE_UUID]
    run.save(update_fields=update_fields)


def find_and_handle(
    msg,
    started_flows=None,
    triggered_start=False,
    resume_parent_run=False,
    user_input=True,
    trigger_send=True,
    continue_parent=True,
):
    from temba.flows.models import Flow, FlowRun

    if started_flows is None:
        started_flows = []

    for run in FlowRun.get_active_for_contact(msg.contact):
        flow = run.flow
        flow.ensure_current_version()

        # it's possible Flow.start is in the process of creating a run for this contact, in which case
        # record this message has handled so it doesn't start any new flows
        if not run.path:  # pragma: no cover
            if run.created_on > timezone.now() - timedelta(minutes=10):
                return True, []
            else:
                return False, []

        last_step = run.path[-1]
        destination = Flow.get_node(flow, last_step[FlowRun.PATH_NODE_UUID], Flow.NODE_TYPE_RULESET)

        # this node doesn't exist anymore, mark it as left so they leave the flow
        if not destination:  # pragma: no cover
            run.set_completed(exit_uuid=None)
            return True, []

        (handled, msgs) = _handle_destination(
            destination,
            run,
            msg,
            started_flows,
            user_input=user_input,
            triggered_start=triggered_start,
            resume_parent_run=resume_parent_run,
            trigger_send=trigger_send,
            continue_parent=continue_parent,
        )

        if handled:
            return True, msgs

    return False, []


def _handle_destination(
    destination,
    run,
    msg,
    started_flows=None,
    user_input=False,
    triggered_start=False,
    trigger_send=True,
    resume_parent_run=False,
    continue_parent=True,
):
    from temba.flows.models import Flow, FlowRun, FlowException

    def add_to_path(path, uuid):
        if uuid in path:
            path.append(uuid)
            raise FlowException("Flow cycle detected at runtime: %s" % path)
        path.append(uuid)

    path = []
    msgs = []

    # lookup our next destination
    handled = False

    while destination:
        result = {"handled": False}

        if destination.get_step_type() == Flow.NODE_TYPE_RULESET:
            should_pause = False

            # check if we need to stop
            if destination.is_pause():
                should_pause = True

            if user_input or not should_pause:
                result = Flow.handle_ruleset(destination, run, msg, started_flows, resume_parent_run)
                add_to_path(path, destination.uuid)

                # add any messages generated by this ruleset
                msgs += result.get("msgs", [])

            # if we used this input, then mark our user input as used
            if should_pause:
                user_input = False

                # once we handle user input, reset our path
                path = []

        elif destination.get_step_type() == Flow.NODE_TYPE_ACTIONSET:
            result = Flow.handle_actionset(destination, run, msg, started_flows)
            add_to_path(path, destination.uuid)

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

        resume_parent_run = False

    # if we have a parent to continue, do so
    if getattr(run, "continue_parent", False) and continue_parent:
        msgs += FlowRun.continue_parent_flow_run(run, trigger_send=False, continue_parent=True)

    # send any messages generated
    if msgs and trigger_send:
        msgs.sort(key=lambda message: message.created_on)
        Msg.objects.filter(id__in=[m.id for m in msgs]).exclude(status=DELIVERED).update(status=PENDING)
        run.flow.org.trigger_send(msgs)

    return handled, msgs


def call_webhook(run, webhook_url, ruleset, msg, action="POST", resthook=None, headers=None):
    from temba.api.models import WebHookEvent, WebHookResult
    from temba.flows.models import Flow

    flow = run.flow
    contact = run.contact
    org = flow.org
    channel = msg.channel if msg else None
    contact_urn = msg.contact_urn if (msg and msg.contact_urn) else contact.get_urn()

    contact_dict = dict(uuid=contact.uuid, name=contact.name)
    if contact_urn:
        contact_dict["urn"] = contact_urn.urn

    post_data = {
        "contact": contact_dict,
        "flow": dict(name=flow.name, uuid=flow.uuid, revision=flow.revisions.order_by("revision").last().revision),
        "path": run.path,
        "results": run.results,
        "run": dict(uuid=str(run.uuid), created_on=run.created_on.isoformat()),
    }

    if msg and msg.id > 0:
        post_data["input"] = dict(
            urn=msg.contact_urn.urn if msg.contact_urn else None, text=msg.text, attachments=(msg.attachments or [])
        )

    if channel:
        post_data["channel"] = dict(name=channel.name, uuid=channel.uuid)

    if not action:  # pragma: needs cover
        action = "POST"

    if resthook:
        WebHookEvent.objects.create(org=org, data=post_data, action=action, resthook=resthook)

    status_code = -1
    message = "None"
    body = None
    request = ""

    start = time.time()

    # webhook events fire immediately since we need the results back
    try:
        # no url, bail!
        if not webhook_url:
            raise ValueError("No webhook_url specified, skipping send")

        # only send webhooks when we are configured to, otherwise fail
        if settings.SEND_WEBHOOKS:
            requests_headers = http_headers(extra=headers)

            s = requests.Session()

            # some hosts deny generic user agents, use Temba as our user agent
            if action == "GET":
                prepped = requests.Request("GET", webhook_url, headers=requests_headers).prepare()
            else:
                requests_headers["Content-type"] = "application/json"
                prepped = requests.Request(
                    "POST", webhook_url, data=json.dumps(post_data), headers=requests_headers
                ).prepare()

            request = prepped_request_to_str(prepped)
            response = s.send(prepped, timeout=10)
            body = response.text
            if body:
                body = body.strip()
            status_code = response.status_code

        else:
            print("!! Skipping WebHook send, SEND_WEBHOOKS set to False")
            body = "Skipped actual send"
            status_code = 200

        if ruleset:
            run.update_fields({Flow.label_to_slug(ruleset.label): body}, do_save=False)
        new_extra = {}

        # process the webhook response
        try:
            response_json = json.loads(body)

            # only update if we got a valid JSON dictionary or list
            if not isinstance(response_json, dict) and not isinstance(response_json, list):
                raise ValueError("Response must be a JSON dictionary or list, ignoring response.")

            new_extra = response_json
            message = "Webhook called successfully."
        except ValueError:
            message = "Response must be a JSON dictionary, ignoring response."

        run.update_fields(new_extra)

        if not (200 <= status_code < 300):
            message = "Got non 200 response (%d) from webhook." % response.status_code
            raise ValueError("Got non 200 response (%d) from webhook." % response.status_code)

    except (requests.ReadTimeout, ValueError) as e:
        message = f"Error calling webhook: {str(e)}"

    except Exception as e:
        logger.error(f"Could not trigger flow webhook: {str(e)}", exc_info=True)

        message = "Error calling webhook: %s" % str(e)

    finally:
        # make sure our message isn't too long
        if message:
            message = message[:255]

        if body is None:
            body = message

        request_time = (time.time() - start) * 1000

        contact = None
        if run:
            contact = run.contact

        result = WebHookResult.objects.create(
            contact=contact,
            url=webhook_url,
            status_code=status_code,
            response=body,
            request=request,
            request_time=request_time,
            org=run.org,
        )

    return result
