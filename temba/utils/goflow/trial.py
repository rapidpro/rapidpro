# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

"""
Temporary functionality to help us try running some flows against a flowserver instance and comparing the results, path
and events with what the current engine produces.
"""

import json
import logging
import six
import time

from django.conf import settings
from raven.contrib.django.raven_compat.models import client as raven_client
from .client import get_client, Events
from .serialize import serialize_contact, serialize_environment, serialize_channel_ref

logger = logging.getLogger(__name__)


def resume_and_report(run, session, msg_in=None, expired_child_run=None):  # pragma: no cover
    try:
        resume_output = resume(run.org, session, msg_in, expired_child_run)

        differences = compare_run(run, resume_output)
        if differences:
            raven_client.captureMessage("differences detected on run #%d" % run.id, extra=differences)

    except Exception as e:
        run_uuid = session['runs'][-1]['uuid']
        logger.error("flowserver exception during trial resumption of run %s: %s" % (run_uuid, six.text_type(e)), exc_info=True)


def resume(org, session, msg_in=None, expired_run=None):
    """
    Resumes the given waiting session with either a message or an expired run
    """
    client = get_client()

    # build request to flow server
    asset_timestamp = int(time.time() * 1000000)
    request = client.request_builder(org, asset_timestamp).asset_server()

    if settings.TESTING:
        request.include_all()

    # only include message if it's a real message
    if msg_in and msg_in.created_on:
        request.add_msg_received(msg_in)
    if expired_run:
        request.add_run_expired(expired_run)

    return request.resume(session)


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
        'params': session_root_run.fields,
        'triggered_on': session_root_run.created_on.isoformat(),
    }

    if trigger_run:
        trigger['type'] = 'flow_action'
        trigger['run'] = serialize_run_summary(trigger_run)
    else:
        trigger['type'] = 'manual'

    runs = [serialize_run(r) for r in session_runs]
    runs[-1]['status'] = 'waiting'

    return {
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
        'status': run.status,
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

    differences = {}

    path1, path2 = reduce_path(run.path), reduce_path(session_run['path'])
    if path1 != path2:
        differences['path'] = path1, path2

    results1, results2 = reduce_results(run.results), reduce_results(session_run.get('results', {}))
    if results1 != results2:
        differences['results'] = results1, results2

    events1, events2 = reduce_events(run.events), reduce_events(session_run.get('events', []))
    if events1 != events2:
        differences['events'] = events1, events2

    return differences


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
    return {k: copy_keys(v, {'category', 'name', 'value', 'node_uuid'}) for k, v in six.iteritems(results)}


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
    return {k: v for k, v in six.iteritems(d) if k in keys}
