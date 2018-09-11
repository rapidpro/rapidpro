import logging

from django_redis import get_redis_connection
from jsondiff import diff as jsondiff

from django.conf import settings
from django.utils import timezone

from temba.flows.server.client import Events, FlowServerException, get_client
from temba.flows.server.serialize import serialize_contact, serialize_environment, serialize_ref
from temba.utils import analytics, json

from .utils import copy_keys, reduce_event

logger = logging.getLogger(__name__)


TRIAL_LOCK = "flowserver_trial"
TRIAL_PERIOD = 15  # only perform a trial every 15 seconds


class Trial:
    """
    A trial of resuming a run in the flowserver
    """

    def __init__(self, run, is_simple):
        self.started_on = timezone.now()
        self.run = run
        self.is_simple = is_simple
        self.session_before = reconstruct_session(run)
        self.session_after = None
        self.differences = None


def is_flow_suitable(flow):
    """
    Checks whether the given flow can be trialled in the flowserver
    """
    from temba.flows.models import RuleSet, Flow

    if flow.flow_type in (Flow.TYPE_VOICE, Flow.TYPE_USSD, Flow.TYPE_SURVEY):
        return False

    for rule_set in flow.rule_sets.all():
        if rule_set.ruleset_type == RuleSet.TYPE_AIRTIME:
            return False

    return True


def is_flow_simple(flow):
    from temba.flows.models import RuleSet, StartFlowAction, TriggerFlowAction, WebhookAction

    for rule_set in flow.rule_sets.all():
        if rule_set.ruleset_type in (RuleSet.TYPE_SUBFLOW, RuleSet.TYPE_WEBHOOK, RuleSet.TYPE_RESTHOOK):
            return False
    for action_set in flow.action_sets.all():
        for action in action_set.actions:
            if action["type"] in (StartFlowAction.TYPE, TriggerFlowAction, WebhookAction.TYPE):  # pragma: no cover
                return False

    return True


def maybe_start(run):
    """
    Either starts a new trial resume or returns nothing
    """
    if settings.FLOW_SERVER_TRIAL == "off":
        return None
    elif settings.FLOW_SERVER_TRIAL == "on":
        # very basic throttling
        r = get_redis_connection()
        if not r.set(TRIAL_LOCK, "x", TRIAL_PERIOD, nx=True):
            return None

        if not is_flow_suitable(run.flow):  # pragma: no cover
            r.delete(TRIAL_LOCK)
            return None

    elif settings.FLOW_SERVER_TRIAL == "always":
        pass

    try:
        is_simple = is_flow_simple(run.flow)

        logger.info(f"Starting flowserver trial resume for run {str(run.uuid)} in flow '{run.flow.name}'")
        return Trial(run, is_simple)

    except Exception as e:
        logger.error(f"Unable to reconstruct session for run {str(run.uuid)}: {str(e)}", exc_info=True)
        return None


def end(trial, msg_in=None, expired_child_run=None):
    """
    Ends a trial resume by performing the resumption in the flowserver and comparing the differences
    """
    try:
        trial.session_after = resume(trial, msg_in, expired_child_run)
        trial.differences = compare(trial.run, trial.session_after)

        if trial.differences:
            report_failure(trial)
            return False
        else:
            report_success(trial)
            return True

    except FlowServerException as e:
        logger.error("Trial resume in flowserver caused server error", extra=e.as_json())
        return False
    except Exception as e:
        logger.error(f"Exception during trial resumption of run {str(trial.run.uuid)}: {str(e)}", exc_info=True)
        return False


def report_success(trial):  # pragma: no cover
    """
    Reports a trial success... essentially a noop but useful for mocking in tests
    """
    logger.info(f"Trial resume for run {str(trial.run.uuid)} in flow '{trial.run.flow.name}' succeeded")

    analytics.gauge("temba.flowserver_trial.resume_pass")


def report_failure(trial):  # pragma: no cover
    """
    Reports a trial failure to sentry
    """
    logger.error(
        f"Trial resume (simple={'yes' if trial.is_simple else 'no'}) in flowserver produced different output",
        extra={
            "org": trial.run.org.name,
            "flow": {"uuid": str(trial.run.flow.uuid), "name": trial.run.flow.name},
            "run_id": trial.run.id,
            "differences": trial.differences,
        },
    )

    if trial.is_simple:
        analytics.gauge("temba.flowserver_trial.resume_simple_fail")
    else:
        analytics.gauge("temba.flowserver_trial.resume_fail")


def resume(trial, msg_in=None, expired_child_run=None):
    """
    Resumes the given waiting session with either a message or an expired run
    """
    org = trial.run.org
    session = trial.session_before
    webhook_mocks = create_webhook_mocks(trial)

    client = get_client()

    # build request to flow server
    request = (
        client.request_builder(org)
        .asset_server()
        .set_config("disable_webhooks", True)
        .set_config("webhook_mocks", webhook_mocks)
    )

    if settings.TESTING:
        request.include_all()

    # only include message if it's a real message
    if msg_in and msg_in.created_on:
        request.add_msg_received(msg_in)
    if expired_child_run:
        request.add_run_expired(expired_child_run)

    return request.resume(session).session


def create_webhook_mocks(trial):
    """
    Creates webhook mocks for goflow from the webhook results created since this trial started
    """
    from temba.api.models import WebHookResult

    results = (
        WebHookResult.objects.filter(contact=trial.run.contact, created_on__gte=trial.started_on)
        .select_related("event")
        .order_by("created_on")
    )

    # create a webhook mock for each webhook result created since the trial started
    return [{"method": r.event.action, "url": r.url, "status": r.status_code, "body": r.body} for r in results]


def get_session_runs(run):
    """
    Get all the runs that make up the given run's session or its trigger
    """
    from temba.flows.models import FlowRun

    session_runs = [run]  # include the run itself
    trigger_run = None

    # include all of its children
    session_runs += list(FlowRun.objects.filter(parent=run, contact=run.contact))

    r = run
    while r.parent:
        if r.parent.contact == r.contact:
            session_runs.append(r.parent)
        else:
            trigger_run = r.parent
            break
        r = r.parent

    session_runs = sorted(session_runs, key=lambda r: r.created_on)
    return session_runs, trigger_run


def reconstruct_session(run):
    """
    Reconstruct session JSON from the given resumable run which is assumed to be WAITING
    """

    # get all the runs that would be in the same session or part of the trigger
    session_runs, trigger_run = get_session_runs(run)
    session_root_run = session_runs[0]

    trigger = {
        "contact": serialize_contact(run.contact),
        "environment": serialize_environment(run.org),
        "flow": {"uuid": str(session_root_run.flow.uuid), "name": session_root_run.flow.name},
        "triggered_on": session_root_run.created_on.isoformat(),
        "params": session_root_run.fields,
    }

    if trigger_run:
        trigger["type"] = "flow_action"
        trigger["run"] = serialize_run_summary(trigger_run)
    else:
        trigger["type"] = "manual"

    runs = [serialize_run(r) for r in session_runs]
    runs[-1]["status"] = "waiting"

    session = {
        "contact": serialize_contact(run.contact),
        "environment": serialize_environment(run.org),
        "runs": runs,
        "status": "waiting",
        "trigger": trigger,
        "wait": {"timeout_on": run.timeout_on.isoformat() if run.timeout_on else None, "type": "msg"},
    }

    # ensure that we are a deep copy - i.e. subsequent changes to the run won't affect this snapshot of session state
    return json.loads(json.dumps(session))


def serialize_run(run):
    serialized = {
        "uuid": str(run.uuid),
        "status": "completed" if run.exited_on else "active",
        "created_on": run.created_on.isoformat(),
        "exited_on": run.exited_on.isoformat() if run.exited_on else None,
        "expires_on": run.expires_on.isoformat() if run.expires_on else None,
        "flow": {"uuid": str(run.flow.uuid), "name": run.flow.name},
        "path": run.path,
    }

    if run.results:
        serialized["results"] = run.results
    if run.events:
        serialized["events"] = run.events
    if run.parent_id and run.parent.contact == run.contact:
        serialized["parent_uuid"] = str(run.parent.uuid)

    msg_in = run.get_last_msg()
    if msg_in:
        serialized["input"] = serialize_input(msg_in)

    # things in @extra might not have come from a webhook call but at least they'll be accessible if we make one
    if run.fields:
        payload = json.dumps(run.fields)
        serialized["webhook"] = {
            "request": "GET / HTTP/1.1\r\nHost: fakewebhooks.com\r\nUser-Agent: goflow-trials\r\n\r\n",
            "response": "HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n\r\n" + payload,
            "status": "success",
            "status_code": 200,
            "url": "http://fakewebhooks.com/",
        }

    return serialized


def serialize_input(msg):
    serialized = {"created_on": msg.created_on.isoformat(), "text": msg.text, "type": "msg", "uuid": str(msg.uuid)}

    if msg.channel_id:
        serialized["channel"] = serialize_ref(msg.channel)
    if msg.contact_urn_id:
        serialized["urn"] = msg.contact_urn.urn
    if msg.attachments:
        serialized["attachments"] = msg.attachments

    return serialized


def serialize_run_summary(run):
    return {
        "uuid": str(run.uuid),
        "flow": {"uuid": str(run.flow.uuid), "name": run.flow.name},
        "contact": serialize_contact(run.contact),
        "status": "active",
        "results": run.results,
    }


def compare(run, session):
    """
    Compares the given run with the given session JSON from the flowserver and returns a dict of problems
    """
    # find equivalent run in the session
    session_run = None
    for r in session["runs"]:
        if r["uuid"] == str(run.uuid):
            session_run = r
            break

    if not session_run:
        return {"session": "run %s not found" % str(run.uuid)}

    legacy_run = {
        "path": reduce_path(run.path),
        "results": reduce_results(run.results),
        "events": reduce_events(run.events),
    }

    new_run = {
        "path": reduce_path(session_run["path"]),
        "results": reduce_results(session_run.get("results", {})),
        "events": reduce_events(session_run.get("events", [])),
    }

    diffs = jsondiff(legacy_run, new_run)
    if not diffs:
        return None

    return {"diffs": diffs, "legacy": legacy_run, "new": new_run}


def reduce_path(path):
    """
    Reduces path to just node/exit. Other fields are datetimes or generated step UUIDs which are non-deterministic
    """
    return [copy_keys(step, {"node_uuid", "exit_uuid"}) for step in path]


def reduce_results(results):
    """
    Excludes created_on
    """
    return {k: copy_keys(v, {"category", "name", "value", "node_uuid", "input"}) for k, v in results.items()}


def reduce_events(events):
    """
    Excludes all but message events
    """
    reduced = []
    for event in events or []:
        if event["type"] not in (Events.msg_created.name, Events.msg_received.name):
            continue

        reduced.append(reduce_event(event))
    return reduced
