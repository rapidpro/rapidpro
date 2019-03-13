from urllib.parse import urlencode

from twilio.rest import Client

from django.utils.encoding import force_text

from temba.utils.http import HttpEvent


def encode_atom(atom):  # pragma: no cover
    if isinstance(atom, (int, bytes)):
        return atom
    elif isinstance(atom, str):
        return atom.encode("utf-8")
    else:
        raise ValueError("list elements should be an integer, " "binary, or string")


class TembaTwilioRestClient(Client):  # pragma: no cover
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    def request(self, method, uri, **kwargs):
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

        del kwargs["auth"]
        event = HttpEvent(method, uri, data)
        if "/messages" in uri.lower() or "/calls" in uri.lower():
            self.events.append(event)
        resp = super().request(method, uri, auth=self.auth, **kwargs)

        event.url = uri
        event.status_code = resp.status_code
        event.response_body = force_text(resp.content)

        return resp
