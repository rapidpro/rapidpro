from __future__ import absolute_import

import requests
import nexmo

from temba.utils.gsm7 import is_gsm7
from django.utils.http import urlencode


class NexmoClient(nexmo.Client):
    """
    Simple implementation of Nexmo API
    """
    URL = 'https://rest.nexmo.com'
    SEND_URL = 'https://rest.nexmo.com/sms/json'

    def __init__(self, api_key, api_secret, **kwargs):
        kwargs['api_key'] = api_key.strip()
        kwargs['api_secret'] = api_secret.strip()
        nexmo.Client.__init__(self, **kwargs)

    def update_account(self, mo_url, dr_url):
        nexmo.Client.update_settings(moCallBackUrl=mo_url, drCallBackUrl=dr_url)

    def get_numbers(self, pattern=None, size=10):
        params = dict()
        if pattern:
            params['pattern'] = str(pattern).strip('+')
        params['size'] = size

        response = nexmo.Client.get_account_numbers(params=params)

        if int(response.get('count', 0)):
            return response['numbers']
        else:
            return []

    def send_message_via_nexmo(self, from_number, to_number, text):
        from temba.channels.models import SendException

        params = dict(api_key=self.api_key, api_secret=self.api_secret)
        params['from'] = from_number.strip('+')
        params['to'] = to_number.strip('+')
        params['text'] = text
        params['status-report-req'] = 1

        # if this isn't going to work as plaintext, send as unicode instead
        if not is_gsm7(text):
            params['type'] = 'unicode'

        log_params = params.copy()
        log_params['api_secret'] = 'x' * len(log_params['api_secret'])
        log_url = NexmoClient.SEND_URL + '?' + urlencode(log_params)

        try:
            response = requests.get(NexmoClient.SEND_URL, params=params)
            response_json = response.json()
            messages = response_json.get('messages', [])
        except:
            raise SendException(u"Failed sending message: %s" % response.text,
                                method=response.request.method,
                                url=log_url,
                                request=None,
                                response=response.text,
                                response_status=response.status_code)

        if not messages or int(messages[0]['status']) != 0:
            raise SendException(u"Failed sending message, received error status [%s]" % messages[0]['status'],
                                method=response.request.method,
                                url=log_url,
                                request=None,
                                response=response.text,
                                response_status=response.status_code)

        else:
            return messages[0]['message-id'], response

    def search_numbers(self, country, pattern):
        response = nexmo.Client.get_available_numbers(self, pattern=pattern, search_pattern=1,
                                                      features='SMS', country=country)
        numbers = []
        if int(response.get('count', 0)):
            numbers += response['numbers']

        response = nexmo.Client.get_available_numbers(self, pattern=pattern, search_pattern=1,
                                                      features='VOICE', country=country)
        if int(response.get('count', 0)):
            numbers += response['numbers']

        return numbers

    def buy_nexmo_number(self, country, number):
        number = number.lstrip('+')
        params = dict(msisdn=number, country=country)
        nexmo.Client.buy_number(self, params=params)

    def update_nexmo_number(self, country, number, moURL, answerURL):
        number = number.lstrip('+')
        params = dict(moHttpUrl=moURL, msisdn=number, country=country)
        nexmo.Client.update_number(self, params=params)

    def test_credentials(self):
        try:
            self.get_balance()
            return True
        except Exception:
            return False


def __main__():  # pragma: no cover
    n = NexmoClient(api_key='foo', api_secret='foo_secret')
    print "Valid: %s" % n.test_credentials()
    print "Balance: %s" % n.get_balance()

    numbers = n.get_numbers()
    print "Numbers: %s" % numbers

    print "US Numbers: %s" % n.search_numbers('US', None)

    seattle_numbers = n.search_numbers('US', '206')
    print "206 Numbers: %s" % seattle_numbers

    print "CH Numbers: %s" % n.search_numbers('CH', None)

    # print "Buying %s: %s" % (seattle_numbers[0]['msisdn'], n.buy_number('US', seattle_numbers[0]['msisdn']))

    # update the MO for one of our numbers
    print "Updating Number %s: %s" % (numbers[0]['msisdn'], n.update_nexmo_number('US', numbers[0]['msisdn'], 'http://rapidpro.io'))

    # update the MO for our account
    print "Updating Account: %s" % n.update_account("http://rapidpro.io", "http://rapidpro.io")

    # send a message
    print "Sending: %s" % n.send_message_via_nexmo('250788382382', "250788383383", "test")
