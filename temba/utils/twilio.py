
import json
from urllib.parse import urlencode

from twilio.rest import UNSET_TIMEOUT, Calls, Messages, TwilioRestClient
from twilio.rest.resources import Resource, make_twilio_request

from django.utils.encoding import force_text

from temba.utils.http import HttpEvent


def encode_atom(atom):  # pragma: no cover
    if isinstance(atom, (int, bytes)):
        return atom
    elif isinstance(atom, str):
        return atom.encode("utf-8")
    else:
        raise ValueError("list elements should be an integer, " "binary, or string")


class LoggingResource(Resource):  # pragma: no cover
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    def request(self, method, uri, **kwargs):
        """
        Send an HTTP request to the resource.

        :raises: a :exc:`~twilio.TwilioRestException`
        """
        if "timeout" not in kwargs and self.timeout is not UNSET_TIMEOUT:
            kwargs["timeout"] = self.timeout

        data = kwargs.get("data")
        if data is not None:
            udata = {}
            for k, v in data.items():
                key = k.encode("utf-8")
                if isinstance(v, (list, tuple, set)):
                    udata[key] = [encode_atom(x) for x in v]
                elif isinstance(v, (int, bytes, str)):
                    udata[key] = encode_atom(v)
                else:
                    raise ValueError("data should be an integer, " "binary, or string, or sequence ")
            data = urlencode(udata, doseq=True)

        event = HttpEvent(method, uri, data)
        self.events.append(event)
        resp = make_twilio_request(method, uri, auth=self.auth, **kwargs)

        event.url = resp.url
        event.status_code = resp.status_code
        event.response_body = force_text(resp.content)

        if method == "DELETE":
            return resp, {}
        else:
            return resp, json.loads(resp.content)


class LoggingCalls(LoggingResource, Calls):  # pragma: no cover
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class LoggingMessages(LoggingResource, Messages):  # pragma: nocover
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class TembaTwilioRestClient(TwilioRestClient):  # pragma: no cover
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # replace endpoints we want logging for
        self.messages = LoggingMessages(self.account_uri, self.auth, self.timeout)
        self.calls = LoggingCalls(self.account_uri, self.auth, self.timeout)
