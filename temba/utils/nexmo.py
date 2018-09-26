import time
import uuid

import jwt
import nexmo as nx
import requests

from django.utils.encoding import force_bytes

from temba.utils import json


class NexmoValidationError(Exception):
    pass


class NexmoClient(nx.Client):
    """
    Simple implementation of Nexmo API
    """

    URL = "https://rest.nexmo.com"
    SEND_URL = "https://rest.nexmo.com/sms/json"

    def __init__(self, api_key, api_secret, app_id, app_private_key, **kwargs):
        kwargs["key"] = api_key.strip()
        kwargs["secret"] = api_secret.strip()
        kwargs["application_id"] = app_id.strip()
        kwargs["private_key"] = app_private_key
        nx.Client.__init__(self, **kwargs)

    def update_account(self, mo_url, dr_url):  # pragma: needs cover
        nx.Client.update_settings(self, moCallBackUrl=mo_url, drCallBackUrl=dr_url)

    def get_numbers(self, pattern=None, size=10):
        params = dict()
        if pattern:
            params["pattern"] = str(pattern).strip("+")
        params["size"] = size

        try:
            response = nx.Client.get_account_numbers(self, params=params)
        except nx.ClientError as e:
            message = str(e)
            if message.startswith("420") or message.startswith("429"):
                time.sleep(1)
                response = nx.Client.get_account_numbers(self, params=params)
            else:  # pragma: no cover
                raise e

        if int(response.get("count", 0)):
            return response["numbers"]
        else:
            return []

    def search_numbers(self, country, pattern):
        response = nx.Client.get_available_numbers(
            self, country_code=country, pattern=pattern, search_pattern=1, features="SMS", country=country
        )
        numbers = []
        if int(response.get("count", 0)):
            numbers += response["numbers"]

        response = nx.Client.get_available_numbers(
            self, country_code=country, pattern=pattern, search_pattern=1, features="VOICE", country=country
        )
        if int(response.get("count", 0)):
            numbers += response["numbers"]

        return numbers

    def buy_nexmo_number(self, country, number):
        number = number.lstrip("+")
        params = dict(msisdn=number, country=country)
        try:
            nx.Client.buy_number(self, params=params)
        except nx.ClientError as e:
            message = str(e)
            if message.startswith("420") or message.startswith("429"):
                time.sleep(1)
                nx.Client.buy_number(self, params=params)
            else:  # pragma: needs cover
                raise e

    def update_nexmo_number(self, country, number, moURL, app_id):
        number = number.lstrip("+")
        params = dict(
            moHttpUrl=moURL, msisdn=number, country=country, voiceCallbackType="app", voiceCallbackValue=app_id
        )
        try:
            nx.Client.update_number(self, params=params)
        except nx.ClientError as e:
            message = str(e)
            if message.startswith("420") or message.startswith("429"):
                time.sleep(2)
                nx.Client.update_number(self, params=params)
            else:  # pragma: needs cover
                raise e

    def test_credentials(self):  # pragma: needs cover
        try:
            self.get_balance()
            return True
        except Exception:
            return False

    def download_recording(self, url, params=None, **kwargs):
        return requests.get(url, params=params, headers=self.gen_headers())

    def gen_headers(self):
        iat = int(time.time())

        payload = dict(self.auth_params)
        payload.setdefault("application_id", self.application_id)
        payload.setdefault("iat", iat)
        payload.setdefault("exp", iat + 60)
        payload.setdefault("jti", str(uuid.uuid4()))

        token = jwt.encode(payload, self.private_key, algorithm="RS256")

        return dict(self.headers, Authorization=b"Bearer " + force_bytes(token))


def __main__():  # pragma: no cover
    n = NexmoClient(api_key="foo", api_secret="foo_secret")
    print("Valid: %s" % n.test_credentials())
    print("Balance: %s" % n.get_balance())

    numbers = n.get_numbers()
    print("Numbers: %s" % numbers)

    print("US Numbers: %s" % n.search_numbers("US", None))

    seattle_numbers = n.search_numbers("US", "206")
    print("206 Numbers: %s" % seattle_numbers)

    print("CH Numbers: %s" % n.search_numbers("CH", None))

    # print "Buying %s: %s" % (seattle_numbers[0]['msisdn'], n.buy_number('US', seattle_numbers[0]['msisdn']))

    # update the MO for one of our numbers
    print(
        "Updating Number %s: %s"
        % (numbers[0]["msisdn"], n.update_nexmo_number("US", numbers[0]["msisdn"], "http://rapidpro.io"))
    )

    # update the MO for our account
    print("Updating Account: %s" % n.update_account("http://rapidpro.io", "http://rapidpro.io"))

    # send a message
    print("Sending: %s" % n.send_message_via_nexmo("250788382382", "250788383383", "test"))


class NCCOException(Exception):
    pass


class NCCOResponse(object):
    def __init__(self, **kwargs):
        self.document = []

    def __str__(self):

        object_len = len(self.document)
        for idx in range(object_len):
            action_dict = self.document[idx]

            if action_dict["action"] in ["talk", "stream"]:
                if idx == object_len - 1:
                    self.document[idx]["bargeIn"] = False
                elif idx <= object_len - 2:
                    next_action_dict = self.document[idx + 1]
                    if next_action_dict["action"] != "input":
                        self.document[idx]["bargeIn"] = False

        return json.dumps(self.document)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def join(self, response):
        self.document = response.document + self.document
        return self

    def say(self, text, **kwargs):
        self.document.append(dict(action="talk", text=str(text), bargeIn=True))
        return self

    def play(self, url=None, digits=None, **kwargs):
        if url is None and digits is None:
            raise NCCOException("Please specify either a url or digits to play.")

        result = dict()
        if url:
            result["action"] = "stream"
            result["streamUrl"] = [url]
            result["bargeIn"] = True

        elif digits:
            result["bargeIn"] = True
            result["action"] = "talk"
            result["text"] = digits
        self.document.append(result)
        return self

    def pause(self, **kwargs):
        return self

    def redirect(self, url=None, **kwargs):
        result = dict(
            action="input",
            maxDigits=1,
            timeOut=1,
            eventUrl=["%s%sinput_redirect=1" % (url, "?" if "?" not in url else "&")],
        )

        self.document.append(result)
        return self

    def hangup(self, **kwargs):
        return self

    def reject(self, reason=None, **kwargs):
        self.hangup()
        return self

    def gather(self, **kwargs):

        result = dict(action="input")

        if kwargs.get("action", False):
            method = kwargs.get("method", "post")
            result["eventMethod"] = method
            result["eventUrl"] = [kwargs.get("action")]

        result["submitOnHash"] = kwargs.get("finish_on_key", "#") == "#"

        if kwargs.get("num_digits", False):
            result["maxDigits"] = int(str(kwargs.get("num_digits")))

        if kwargs.get("timeout", False):
            result["timeOut"] = int(str(kwargs.get("timeout")))

        self.document.append(result)
        return self

    def record(self, **kwargs):
        result = dict(format="wav", endOnSilence=4, endOnKey="#", beepStart=True, action="record")

        if kwargs.get("max_length", False):
            result["timeOut"] = int(str(kwargs.get("max_length")))

        if kwargs.get("action", False):
            method = kwargs.get("method", "post")
            result["eventMethod"] = method
            result["eventUrl"] = [kwargs.get("action")]

        self.document.append(result)
        result = dict(
            action="input",
            maxDigits=1,
            timeOut=1,
            eventUrl=[
                "%s%ssave_media=1" % (kwargs.get("action"), "?" if "?" not in str(kwargs.get("action")) else "&")
            ],
        )

        self.document.append(result)

        return self
