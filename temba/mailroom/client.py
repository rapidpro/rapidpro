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

    def version(self):
        return self._request("", post=False).get("version")

    def expression_migrate(self, expression):
        """
        Migrates a legacy expression to latest engine version
        """
        if not expression:
            return ""

        try:
            resp = self._request("expression/migrate", {"expression": expression})
            return resp["migrated"]
        except FlowValidationException:
            # if the expression is invalid.. just return original
            return expression

    def flow_migrate(self, definition, to_version=None):
        """
        Migrates a flow definition to the specified spec version
        """
        from temba.flows.models import Flow

        if not to_version:
            to_version = Flow.CURRENT_SPEC_VERSION

        return self._request("flow/migrate", {"flow": definition, "to_version": to_version})

    def flow_inspect(self, org_id, flow):
        payload = {"flow": flow}

        # can't do dependency checking during tests because mailroom can't see unit test data created in a transaction
        if not settings.TESTING:
            payload["org_id"] = org_id

        return self._request("flow/inspect", payload)

    def flow_clone(self, flow, dependency_mapping):
        payload = {"flow": flow, "dependency_mapping": dependency_mapping}

        return self._request("flow/clone", payload)

    def sim_start(self, payload):
        return self._request("sim/start", payload)

    def sim_resume(self, payload):
        return self._request("sim/resume", payload)

    def contact_search(self, org_id, group_uuid, query, sort, offset=0):
        payload = {"org_id": org_id, "group_uuid": group_uuid, "query": query, "sort": sort, "offset": offset}

        return self._request("contact/search", payload)

    def parse_query(self, org_id, query, group_uuid=""):
        payload = {"org_id": org_id, "query": query, "group_uuid": group_uuid}

        return self._request("contact/parse_query", payload)

    def _request(self, endpoint, payload=None, post=True):
        if logger.isEnabledFor(logging.DEBUG):  # pragma: no cover
            logger.debug("=============== %s request ===============" % endpoint)
            logger.debug(json.dumps(payload, indent=2))
            logger.debug("=============== /%s request ===============" % endpoint)

        req_fn = requests.post if post else requests.get
        response = req_fn("%s/mr/%s" % (self.base_url, endpoint), json=payload, headers=self.headers)
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
