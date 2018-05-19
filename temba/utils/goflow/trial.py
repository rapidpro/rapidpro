
"""
Temporary functionality to help us try running some flows against a flowserver instance and comparing the results, path
and events with what the current engine produces.
"""

import json
import logging
import time

from django.conf import settings
from django_redis import get_redis_connection
from .client import get_client, Events
from .serialize import serialize_contact, serialize_environment, serialize_channel_ref
from jsondiff import diff as jsondiff

logger = logging.getLogger(__name__)


TRIAL_LOCK = 'flowserver_trial'
TRIAL_PERIOD = 60  # only perform a trial every 1 minute


class ResumeTrial(object):
    """
    A trial of resuming a run in the flowserver
    """
    def __init__(self, run):
        self.run = run
        self.session_before = reconstruct_session(run)
        self.session_after = None
        self.differences = None


def is_flow_suitable(flow):
    """
    Checks whether the given flow can be trialled in the flowserver
    """
    from temba.flows.models import WebhookAction, TriggerFlowAction, StartFlowAction, RuleSet, Flow

    if flow.flow_type not in (Flow.FLOW, Flow.MESSAGE):
        return False

    for action_set in flow.action_sets.all():
        for action in action_set.get_actions():
            if action.TYPE in (WebhookAction.TYPE, TriggerFlowAction.TYPE, StartFlowAction.TYPE):
                return False

    for rule_set in flow.rule_sets.all():
        if rule_set.ruleset_type in (RuleSet.TYPE_AIRTIME, RuleSet.TYPE_WEBHOOK, RuleSet.TYPE_RESTHOOK, RuleSet.TYPE_SUBFLOW):
            return False

    return True


def maybe_start_resume(run):
    """
    Either starts a new trial resume or returns nothing
    """
    if settings.FLOW_SERVER_TRIAL == 'off':
        return None
    elif settings.FLOW_SERVER_TRIAL == 'on':
        # very basic throttling
        r = get_redis_connection()
        if not r.set(TRIAL_LOCK, 'x', TRIAL_PERIOD, nx=True):
            return None

        if not is_flow_suitable(run.flow):
            r.delete(TRIAL_LOCK)
            return None

    elif settings.FLOW_SERVER_TRIAL == 'always':
        pass

    try:
        print("Starting flowserver trial resume for run %s" % str(run.uuid))
        return ResumeTrial(run)

    except Exception as e:
        logger.error("unable to reconstruct session for run %s: %s" % (str(run.uuid), str(e)), exc_info=True)
        return None


def end_resume(trial, msg_in=None, expired_child_run=None):
    """
    Ends a trial resume by performing the resumption in the flowserver and comparing the differences
    """
    try:
        trial.session_after = resume(trial.run.org, trial.session_before, msg_in, expired_child_run)
        trial.differences = compare_run(trial.run, trial.session_after)

        if trial.differences:
            report_failure(trial)
            return False
        else:
            report_success(trial)
            return True

    except Exception as e:
        logger.error("flowserver exception during trial resumption of run %s: %s" % (str(trial.run.uuid), str(e)), exc_info=True)
        return False


def report_success(trial):  # pragma: no cover
    """
    Reports a trial success... essentially a noop but useful for mocking in tests
    """
    print("Flowserver trial resume for run %s succeeded" % str(trial.run.uuid))


def report_failure(trial):  # pragma: no cover
    """
    Reports a trial failure to sentry
    """
    print("Flowserver trial resume for run %s failed" % str(trial.run.uuid))

    logger.error("trial resume in flowserver produced different output", extra={
        'run_id': trial.run.id,
        'differences': trial.differences
    })


def resume(org, session, msg_in=None, expired_child_run=None):
    """
    Resumes the given waiting session with either a message or an expired run
    """
    client = get_client()

    # build request to flow server
    asset_timestamp = int(time.time() * 1000000)
    request = client.request_builder(org, asset_timestamp).asset_server().set_config('disable_webhooks', True)

    if settings.TESTING:
        request.include_all()

    # only include message if it's a real message
    if msg_in and msg_in.created_on:
        request.add_msg_received(msg_in)
    if expired_child_run:
        request.add_run_expired(expired_child_run)

    return request.resume(session).session


def reconstruct_session(run):
    """
    Reconstruct session JSON from the given resumable run which is assumed to be WAITING
    """
    from temba.flows.models import FlowRun

    # get all the runs that would be in the same session or part of the trigger
    trigger_run = None
    session_runs = [run]
    session_runs += list(FlowRun.objects.filter(parent=run, contact=run.contact))
    if run.parent:
        if run.parent.contact == run.contact:
            session_runs.append(run.parent)
        else:
            trigger_run = run.parent

    session_runs = sorted(session_runs, key=lambda r: r.created_on)
    session_root_run = session_runs[0]

    trigger = {
        'contact': serialize_contact(run.contact),
        'environment': serialize_environment(run.org),
        'flow': {'uuid': str(session_root_run.flow.uuid), 'name': session_root_run.flow.name},
        'triggered_on': session_root_run.created_on.isoformat(),
        'params': session_root_run.fields
    }

    if trigger_run:
        trigger['type'] = 'flow_action'
        trigger['run'] = serialize_run_summary(trigger_run)
    else:
        trigger['type'] = 'manual'

    runs = [serialize_run(r) for r in session_runs]
    runs[-1]['status'] = 'waiting'

    session = {
        'contact': serialize_contact(run.contact),
        'environment': serialize_environment(run.org),
        'runs': runs,
        'status': 'waiting',
        'trigger': trigger,
        'wait': {
            'timeout_on': run.timeout_on.isoformat() if run.timeout_on else None,
            'type': 'msg'
        }
    }

    # ensure that we are a deep copy - i.e. subsequent changes to the run won't affect this snapshot of session state
    return json.loads(json.dumps(session))


def serialize_run(run):
    serialized = {
        'uuid': str(run.uuid),
        'status': 'completed' if run.exited_on else 'active',
        'created_on': run.created_on.isoformat(),
        'exited_on': run.exited_on.isoformat() if run.exited_on else None,
        'expires_on': run.expires_on.isoformat() if run.expires_on else None,
        'flow': {'uuid': str(run.flow.uuid), 'name': run.flow.name},
        'path': run.path,
    }

    if run.results:
        serialized['results'] = run.results
    if run.events:
        serialized['events'] = run.events
    if run.parent_id and run.parent.contact == run.contact:
        serialized['parent_uuid'] = str(run.parent.uuid)

    msg_in = run.get_last_msg()
    if msg_in:
        serialized['input'] = serialize_input(msg_in)

    # things in @extra might not have come from a webhook call but at least they'll be accessible if we make one
    if run.fields:
        payload = json.dumps(run.fields)
        serialized['webhook'] = {
            'request': "GET / HTTP/1.1\r\nHost: fakewebhooks.com\r\nUser-Agent: goflow-trials\r\n\r\n",
            'response': "HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n\r\n" + payload,
            'status': "success",
            'status_code': 200,
            'url': "http://fakewebhooks.com/"
        }

    return serialized


def serialize_input(msg):
    serialized = {
        'created_on': msg.created_on.isoformat(),
        'text': msg.text,
        'type': 'msg',
        'uuid': str(msg.uuid)
    }

    if msg.channel_id:
        serialized['channel'] = serialize_channel_ref(msg.channel)
    if msg.contact_urn_id:
        serialized['urn'] = msg.contact_urn.urn
    if msg.attachments:
        serialized['attachments'] = msg.attachments

    return serialized


def serialize_run_summary(run):
    return {
        'uuid': str(run.uuid),
        'flow': {'uuid': str(run.flow.uuid), 'name': run.flow.name},
        'contact': serialize_contact(run.contact),
        'status': 'active',
        'results': run.results,
    }


def compare_run(run, session):
    """
    Compares the given run with the given session JSON from the flowserver and returns a dict of problems
    """
    # find equivalent run in the session
    session_run = None
    for r in session['runs']:
        if r['uuid'] == str(run.uuid):
            session_run = r
            break

    if not session_run:
        return {'session': "run %s not found" % str(run.uuid)}

    rapidpro_run = {
        'path': reduce_path(run.path),
        'results': reduce_results(run.results),
        'events': reduce_events(run.events),
    }

    goflow_run = {
        'path': reduce_path(session_run['path']),
        'results': reduce_results(session_run.get('results', {})),
        'events': reduce_events(session_run.get('events', [])),
    }

    diffs = jsondiff(rapidpro_run, goflow_run)
    if not diffs:
        return None

    return {
        'diffs': diffs,
        'goflow': goflow_run,
        'rapidpro': rapidpro_run,
    }


def reduce_path(path):
    """
    Reduces path to just node/exit. Other fields are datetimes or generated step UUIDs which are non-deterministic
    """
    reduced = [copy_keys(step, {'node_uuid', 'exit_uuid'}) for step in path]

    # rapidpro doesn't set exit_uuid on terminal actions
    if 'exit_uuid' in reduced[-1]:
        del reduced[-1]['exit_uuid']

    return reduced


def reduce_results(results):
    """
    Excludes input because rapidpro uses last message but flowserver uses operand, and created_on
    """
    return {k: copy_keys(v, {'category', 'name', 'value', 'node_uuid'}) for k, v in results.items()}


def reduce_events(events):
    """
    Excludes all but message events
    """
    reduced = []
    for event in events:
        if event['type'] not in (Events.msg_created.name, Events.msg_received.name):
            continue

        new_event = copy_keys(event, {'type', 'msg'})
        new_event['msg'] = copy_keys(event['msg'], {'text', 'urn', 'channel', 'attachments'})

        reduced.append(new_event)
    return reduced


def copy_keys(d, keys):
    return {k: v for k, v in d.items() if k in keys}
