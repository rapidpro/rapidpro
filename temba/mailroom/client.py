import logging

import requests

from django.conf import settings

from temba.utils import json

logger = logging.getLogger(__name__)


class MailroomException(Exception):
    """
    Exception for failed requests to mailroom
    """

    def __init__(self, endpoint, request, response):
        self.endpoint = endpoint
        self.request = request
        self.response = response

    def as_json(self):
        return {"endpoint": self.endpoint, "request": self.request, "response": self.response}


class FlowValidationException(MailroomException):
    """
    Exception for a flow validation request that fails validation
    """

    def __init__(self, endpoint, request, response):
        super().__init__(endpoint, request, response)

        self.message = response["error"]

    def __str__(self):
        return self.message


class MailroomClient:
    """
    Basic web client for mailroom
    """

    default_headers = {"User-Agent": "Temba"}

    def __init__(self, base_url, auth_token):
        self.base_url = base_url
        self.headers = self.default_headers.copy()
        if auth_token:
            self.headers["Authorization"] = "Token " + auth_token

    def flow_migrate(self, definition):
        return self._request("flow/migrate", {"flow": definition})

    def flow_inspect(self, flow, validate_with_org=None):
        payload = {"flow": flow}

        # can't do validation during tests because mailroom can't see unit test data created in a transaction
        if validate_with_org and not settings.TESTING:  # pragma: no cover
            payload["validate_with_org_id"] = validate_with_org.id

        return self._request("flow/inspect", payload)

    def flow_clone(self, dependency_mapping, flow, validate_with_org=None):
        payload = {"dependency_mapping": dependency_mapping, "flow": flow}

        # can't do validation during tests because mailroom can't see unit test data created in a transaction
        if validate_with_org and not settings.TESTING:  # pragma: no cover
            payload["validate_with_org_id"] = validate_with_org.id

        return self._request("flow/clone", payload)

    def flow_validate(self, org, definition):
        payload = {"flow": definition}

        # during tests do validation without org because mailroom can't see unit test data created in a transaction
        if org and not settings.TESTING:
            payload["org_id"] = org.id

        validated = self._request("flow/validate", payload)
        validated["_ui"] = definition.get("_ui", {})
        return validated

    def sim_start(self, payload):
        return self._request("sim/start", payload)

    def sim_resume(self, payload):
        return self._request("sim/resume", payload)

    def _request(self, endpoint, payload):
        if logger.isEnabledFor(logging.DEBUG):  # pragma: no cover
            logger.debug("=============== %s request ===============" % endpoint)
            logger.debug(json.dumps(payload, indent=2))
            logger.debug("=============== /%s request ===============" % endpoint)

        response = requests.post("%s/mr/%s" % (self.base_url, endpoint), json=payload, headers=self.headers)
        resp_json = response.json()

        if logger.isEnabledFor(logging.DEBUG):  # pragma: no cover
            logger.debug("=============== %s response ===============" % endpoint)
            logger.debug(json.dumps(resp_json, indent=2))
            logger.debug("=============== /%s response ===============" % endpoint)

        if response.status_code == 422:
            raise FlowValidationException(endpoint, payload, resp_json)
        if 400 <= response.status_code < 500:
            raise MailroomException(endpoint, payload, resp_json)

        response.raise_for_status()

        return resp_json


def get_client():
    return MailroomClient(settings.MAILROOM_URL, settings.MAILROOM_AUTH_TOKEN)
