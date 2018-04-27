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
from .client import get_client, FlowServerException, Events
from .serialize import serialize_contact, serialize_environment, serialize_channel_ref

logger = logging.getLogger(__name__)


def resume(org, session, msg_in=None, expired_child_run=None):
    client = get_client()

    # build request to flow server
    asset_timestamp = int(time.time() * 1000000)
    request = client.request_builder(org, asset_timestamp).asset_server()

    if settings.TESTING:
        request.include_all()

    # only include message if it's a real message
    if msg_in and msg_in.created_on:
        request.add_msg_received(msg_in)
    if expired_child_run:  # pragma: needs cover
        request.add_run_expired(expired_child_run)

    try:
        return request.resume(session)

    except FlowServerException as e:  # pragma: no cover
        logger.error("Flowserver exception: %s" % six.text_type(e), exc_info=True)
        return None


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

    return {
        'contact': serialize_contact(run.contact),
        'environment': serialize_environment(run.org),
        'runs': [serialize_run(r) for r in session_runs],
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
        'status': 'completed' if run.exited_on else 'waiting',
        'created_on': run.created_on.isoformat(),
        'exited_on': run.exited_on.isoformat() if run.exited_on else None,
        'expires_on': run.expires_on.isoformat() if run.expires_on else None,
        'flow': {'uuid': str(run.flow.uuid), 'name': run.flow.name},
        'path': run.path,
        'events': run.events,
        'results': run.results,
    }

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


def compare(run, session):
    """
    Compares the given run with the given session JSON from the flowserver and returns a list of problems
    """
    # find equivalent run in the session
    session_run = None
    for r in session['runs']:
        if r['uuid'] == str(run.uuid):
            session_run = r
            break

    if not session_run:
        return ["run %s not found in session" % str(run.uuid)]

    problems = []
    problems += compare_paths(run.path, session_run['path'])
    problems += compare_results(run.results, session_run.get('results', []))
    problems += compare_events(run.events, session_run.get('events', []))
    return problems


def compare_paths(path1, path2):
    def reduce(path):
        """
        Reduces path to just node/exit. Other fields are datetimes or generated UUIDs which can't be compared
        """
        return [{'node_uuid': step['node_uuid'], 'exit_uuid': step.get('exit_uuid')} for step in path]

    if reduce(path1) != reduce(path2):
        print(json.dumps(path1, indent=2))
        print(json.dumps(path2, indent=2))

        return ["path mismatch (%d steps vs %d steps)" % (len(path1), len(path2))]

    return []


def compare_results(results1, results2):
    def reduce(results):
        for result in six.itervalues(results):
            del result['created_on']

            # flowserver uses operand as result input, rapidpro uses last message
            if 'input' in result:
                del result['input']
        return results

    if reduce(results1) != reduce(results2):
        print(json.dumps(results1, indent=2))
        print(json.dumps(results2, indent=2))

        return ["results mismatch (%d results vs %d results)" % (len(results1), len(results2))]

    return []


def compare_events(events1, events2):
    def reduce(events):
        new_events = []
        for event in events:
            # only include msg events
            if event['type'] not in (Events.msg_created.name, Events.msg_received.name):
                continue

            # remove non-deterministic fields
            del event['created_on']
            del event['msg']['uuid']
            if 'step_uuid' in event:
                del event['step_uuid']

            new_events.append(event)
        return new_events

    if reduce(events1) != reduce(events2):
        print(json.dumps(events1, indent=2))
        print(json.dumps(events2, indent=2))

        return ["events mismatch (%d events vs %d events)" % (len(events1), len(events2))]

    return []
