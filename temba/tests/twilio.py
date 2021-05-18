from twilio.request_validator import RequestValidator
from twilio.rest import Client


class MockRequestValidator(RequestValidator):
    def __init__(self, token):
        pass

    def validate(self, url, post, signature):
        return True


class MockTwilioClient(Client):
    def __init__(self, sid, token, org=None, base=None):
        self.org = org
        self.base = base
        self.auth = ["", "FakeRequestToken"]
        self.events = []

    @property
    def api(self):
        return MockTwilioClient.MockAPI()

    def validate(self, request):
        return True

    class MockInstanceResource(object):
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self):
            return self

        def update(self, **kwargs):
            return True

        def delete(self, **kwargs):
            return True

        def get(self, sid):
            objs = list(self.stream())
            if len(objs) > 0:
                return objs[0]

    class MockAPI(object):
        def __init__(self, *args, **kwargs):
            self.base_url = "base_url"
            pass

        @property
        def account(self):
            return MockTwilioClient.MockAccounts().get("Full")

        @property
        def incoming_phone_numbers(self):
            return MockTwilioClient.MockPhoneNumbers()

        @property
        def available_phone_numbers(self):
            return MockTwilioClient.MockAvailablePhonenumbers()

        @property
        def short_codes(self):
            return MockTwilioClient.MockShortCodes()

        @property
        def applications(self):
            return MockTwilioClient.MockApplications()

        @property
        def calls(self):
            return MockTwilioClient.MockCalls()

        @property
        def messages(self):
            return MockTwilioClient.MockInstanceResource()

    class MockShortCode(MockInstanceResource):
        def __init__(self, short_code):
            self.short_code = short_code
            self.sid = "ShortSid"

    class MockShortCodes(MockInstanceResource):
        def __init__(self, *args):
            pass

        def list(self, short_code=None):
            return [MockTwilioClient.MockShortCode(short_code)]

        def stream(self, *args, **kwargs):
            return iter([MockTwilioClient.MockShortCode("1122")])

        def update(self, sid, **kwargs):
            print("Updating short code with sid %s" % sid)

    class MockCallInstance(MockInstanceResource):
        def __init__(self, *args, **kwargs):
            self.sid = "CallSid"
            pass

        def update(self, status):
            print("Updating call %s to status %s" % (self.sid, status))

    class MockApplication(MockInstanceResource):
        def __init__(self, friendly_name):
            self.friendly_name = friendly_name
            self.sid = "TwilioTestSid"

    class MockPhoneNumber(MockInstanceResource):
        def __init__(self, phone_number):
            self.phone_number = phone_number
            self.sid = "PhoneNumberSid"

    class MockAccount(MockInstanceResource):
        def __init__(self, account_type, auth_token="AccountToken"):
            self.type = account_type
            self.auth_token = auth_token
            self.sid = "AccountSid"

        def get(self, sid):
            return self

        def fetch(self):
            return self

    class MockAccounts(MockInstanceResource):
        def __init__(self, *args):
            pass

        def get(self, account_type):
            return MockTwilioClient.MockAccount(account_type)

    class MockAvailablePhonenumbers(MockInstanceResource):
        def __init__(self, *args):
            self.country_code = None
            self.local = MockTwilioClient.MockPhoneNumbers()
            self.mobile = MockTwilioClient.MockPhoneNumbers()
            self.toll_free = MockTwilioClient.MockPhoneNumbers()

        def __call__(self, country_code):
            self.country_code = country_code
            return self

    class MockPhoneNumbers(MockInstanceResource):
        def __init__(self, *args):
            pass

        def list(self, phone_number=None):
            return [MockTwilioClient.MockPhoneNumber(phone_number)]

        def stream(self, *args, **kwargs):
            return iter([MockTwilioClient.MockPhoneNumber("+12062345678")])

        def search(self, **kwargs):
            return []

        def create(self, *args, **kwargs):
            phone_number = kwargs["phone_number"]
            return MockTwilioClient.MockPhoneNumber(phone_number)

    class MockApplications(MockInstanceResource):
        def __init__(self, *args):
            pass

        def create(self, **kwargs):
            return MockTwilioClient.MockApplication("temba.io/1234")

        def list(self, friendly_name=None):
            return [MockTwilioClient.MockApplication(friendly_name)]

        def get(self, sid):
            return self.list()[0]

    class MockCalls(MockInstanceResource):
        def __init__(self):
            pass

        def get(self, *args):
            return MockTwilioClient.MockCallInstance()

        def create(self, to=None, from_=None, url=None, status_callback=None):
            return MockTwilioClient.MockCallInstance(to=to, from_=from_, url=url, status_callback=status_callback)

        def hangup(self, external_id):
            print("Hanging up %s on Twilio" % external_id)

        def update(self, external_id, url):
            print("Updating call for %s to url %s" % (external_id, url))
