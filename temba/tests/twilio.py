from twilio.request_validator import RequestValidator

from temba.ivr.clients import TwilioClient


class MockRequestValidator(RequestValidator):
    def __init__(self, token):
        pass

    def validate(self, url, post, signature):
        return True


class MockTwilioClient(TwilioClient):
    def __init__(self, sid, token, org=None, base=None):
        self.org = org
        self.base = base
        self.auth = ["", "FakeRequestToken"]
        self.events = []

    @property
    def applications(self):
        return MockTwilioClient.MockApplications()

    @property
    def calls(self):
        return MockTwilioClient.MockCalls()

    @property
    def accounts(self):
        return MockTwilioClient.MockAccounts()

    @property
    def phone_numbers(self):
        return MockTwilioClient.MockPhoneNumbers()

    @property
    def messages(self):
        return MockTwilioClient.MockSMS()

    def validate(self, request):
        return True

    class MockShortCode(object):
        def __init__(self, short_code):
            self.short_code = short_code
            self.sid = "ShortSid"

    class MockShortCodes(object):
        def __init__(self, *args):
            pass

        def list(self, short_code=None):
            return [MockTwilioClient.MockShortCode(short_code)]

        def update(self, sid, **kwargs):
            print("Updating short code with sid %s" % sid)

    class MockSMS(object):
        def __init__(self, *args):
            self.uri = "/SMS"
            self.short_codes = MockTwilioClient.MockShortCodes()

    class MockCallInstance(object):
        def __init__(self, *args, **kwargs):
            self.sid = "CallSid"
            pass

        def update(self, status):
            print("Updating call %s to status %s" % (self.sid, status))

    class MockApplication(object):
        def __init__(self, friendly_name):
            self.friendly_name = friendly_name
            self.sid = "TwilioTestSid"

    class MockPhoneNumber(object):
        def __init__(self, phone_number):
            self.phone_number = phone_number
            self.sid = "PhoneNumberSid"

    class MockAccount(object):
        def __init__(self, account_type, auth_token="AccountToken"):
            self.type = account_type
            self.auth_token = auth_token
            self.sid = "AccountSid"

    class MockAccounts(object):
        def __init__(self, *args):
            pass

        def get(self, account_type):
            return MockTwilioClient.MockAccount(account_type)

    class MockPhoneNumbers(object):
        def __init__(self, *args):
            pass

        def list(self, phone_number=None):
            return [MockTwilioClient.MockPhoneNumber(phone_number)]

        def search(self, **kwargs):
            return []

        def update(self, sid, **kwargs):
            print("Updating phone number with sid %s" % sid)

    class MockApplications(object):
        def __init__(self, *args):
            pass

        def create(self, **kwargs):
            return MockTwilioClient.MockApplication("temba.io/1234")

        def list(self, friendly_name=None):
            return [MockTwilioClient.MockApplication(friendly_name)]

        def delete(self, **kwargs):
            return True

    class MockCalls(object):
        def __init__(self):
            self.events = []

        def get(self, *args):
            return MockTwilioClient.MockCallInstance()

        def create(self, to=None, from_=None, url=None, status_callback=None):
            return MockTwilioClient.MockCallInstance(to=to, from_=from_, url=url, status_callback=status_callback)

        def hangup(self, external_id):
            print("Hanging up %s on Twilio" % external_id)

        def update(self, external_id, url):
            print("Updating call for %s to url %s" % (external_id, url))
