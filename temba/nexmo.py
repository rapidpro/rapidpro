from urllib import urlencode
from urlparse import urljoin
from django.utils.translation import ugettext_lazy as _
import requests
from temba.utils.gsm7 import is_gsm7

class NexmoClient(object):
    """
    Simple implementation of Nexmo API
    """
    URL = 'https://rest.nexmo.com'
    SEND_URL = 'https://rest.nexmo.com/sms/json'

    def __init__(self, api_key, api_secret):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()

    def _validate_response(self, response):
        json = response.json()

        if 'error-code' in json and json.get('error-code', '200') != '200':
            code = json.get('error-code')
            message = json.get('error-code-label', "No more details available.")
            raise Exception(_("Nexmo Error # %s: %s") % (code, message))

        return json

    def _fire_get(self, path, params):
        headers = {'content-type': 'application/json'}
        response = requests.get(urljoin(NexmoClient.URL, path), params=params, headers=headers)
        return self._validate_response(response)

    def _fire_post(self, path, params):
        headers = {'content-type': 'application/json'}
        response = requests.post(urljoin(NexmoClient.URL, path), params=params, headers=headers)
        return self._validate_response(response)

    def update_account(self, mo_url, dr_url):
        path = 'account/settings/%s/%s' % (self.api_key, self.api_secret)
        params = dict(moCallBackUrl=mo_url, drCallBackUrl=dr_url)
        self._fire_post(path, params)

    def get_balance(self):
        path = "/account/get-balance/%s/%s" % (self.api_key, self.api_secret)
        response = self._fire_get(path, {})
        return response['value']

    def get_numbers(self, pattern=None):
        path = "/account/numbers/%s/%s" % (self.api_key, self.api_secret)
        params = dict()
        if pattern:
            params['pattern'] = str(pattern).strip('+')

        response = self._fire_get(path, params)

        if int(response.get('count', 0)):
            return response['numbers']
        else:
            return []

    def send_message(self, from_number, to_number, text):
        from temba.channels.models import SendException

        params = dict(api_key=self.api_key, api_secret=self.api_secret)
        params['from'] = from_number.strip('+')
        params['to'] = to_number.strip('+')
        params['text'] = text
        params['status-report-req'] = 1

        # if this isn't going to work as plaintext, send as unicode instead
        if not is_gsm7(text):
            params['type'] = 'unicode'

        response = requests.get(NexmoClient.SEND_URL, params=params)
        response_json = response.json()

        messages = response_json.get('messages', [])
        if not messages or int(messages[0]['status']) != 0:
            raise SendException(u"Failed sending message, received error status [%s]" % messages[0]['status'],
                                method=response.request.method,
                                url=response.request.url,
                                request=None,
                                response=response.text,
                                response_status=response.status_code)

        else:
            return (messages[0]['message-id'], response)

    def search_numbers(self, country, pattern):
        path = '/number/search/%s/%s/%s?features=SMS' % (self.api_key, self.api_secret, country)
        response = self._fire_get(path, dict(pattern=pattern))
        if int(response.get('count', 0)):
            return response['numbers']
        else:
            return []

    def buy_number(self, country, number):
        number = number.lstrip('+')
        path = '/number/buy/%s/%s/%s/%s' % (self.api_key, self.api_secret, country, number)
        self._fire_post(path, dict())

    def update_number(self, country, number, moURL):
        number = number.lstrip('+')
        path = '/number/update/%s/%s/%s/%s' % (self.api_key, self.api_secret, country, number)
        self._fire_post(path, dict(moHttpUrl=moURL))

    def test_credentials(self):
        try:
            self.get_balance()
            return True
        except Exception as e:
            return False


def __main__():
    n = NexmoClient('foo', 'foo_secret')
    print "Valid: %s" % n.test_credentials()
    print "Balance: %s" % n.get_balance()

    numbers = n.get_numbers()
    print "Numbers: %s" % numbers

    print "US Numbers: %s" % n.search_numbers('US', None)

    seattle_numbers = n.search_numbers('US', '206')
    print "206 Numbers: %s" % seattle_numbers

    print "CH Numbers: %s" % n.search_numbers('CH', None)

    #print "Buying %s: %s" % (seattle_numbers[0]['msisdn'], n.buy_number('US', seattle_numbers[0]['msisdn']))

    # update the MO for one of our numbers
    print "Updating Number %s: %s" % (numbers[0]['msisdn'], n.update_number('US', numbers[0]['msisdn'], 'http://rapidpro.io'))

    # update the MO for our account
    print "Updating Account: %s" % n.update_account("http://rapidpro.io", "http://rapidpro.io")

    # send a message
    print "Sending: %s" % n.send_message('250788382382', "250788383383", "test")
