from enum import Enum

import requests

from django.conf import settings

from temba.utils import json


class Events(Enum):
    broadcast_created = 1
    contact_changed = 2
    contact_channel_changed = 3
    contact_field_changed = 4
    contact_groups_changed = 5
    contact_language_changed = 6
    contact_name_changed = 7
    contact_timezone_changed = 8
    contact_urn_added = 9
    email_created = 10
    environment_changed = 11
    error = 12
    flow_triggered = 13
    input_labels_added = 14
    msg_created = 15
    msg_received = 16
    msg_wait = 17
    nothing_wait = 18
    run_expired = 19
    run_result_changed = 20
    session_triggered = 21
    wait_timed_out = 22
    webhook_called = 23


class MailroomException(Exception):
    def __init__(self, endpoint, request, response):
        self.endpoint = endpoint
        self.request = request
        self.response = response

    def as_json(self):
        return {"endpoint": self.endpoint, "request": self.request, "response": self.response}


class MailroomClient:
    """
    Basic web client for mailroom
    """

    headers = {"User-Agent": "Temba"}

    def __init__(self, base_url, debug=False):
        self.base_url = base_url
        self.debug = debug

    def migrate(self, flow_migrate):
        return self._request("flow/migrate", flow_migrate)

    def _request(self, endpoint, payload):
        if self.debug:
            print("[MAILROOM]=============== %s request ===============" % endpoint)
            print(json.dumps(payload, indent=2))
            print("[MAILROOM]=============== /%s request ===============" % endpoint)

        response = requests.post("%s/mr/%s" % (self.base_url, endpoint), json=payload, headers=self.headers)
        resp_json = response.json()

        if self.debug:
            print("[MAILROOM]=============== %s response ===============" % endpoint)
            print(json.dumps(resp_json, indent=2))
            print("[MAILROOM]=============== /%s response ===============" % endpoint)

        if 400 <= response.status_code < 500:
            raise MailroomException(endpoint, payload, resp_json)

        response.raise_for_status()

        return resp_json


def get_client():
    return MailroomClient(settings.MAILROOM_URL, settings.MAILROOM_DEBUG)
