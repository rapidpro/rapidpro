from __future__ import unicode_literals, absolute_import

import time
import requests
import six

from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _

from temba.channels.types.zenvia.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException


class ZenviaType(ChannelType):
    """
    An Zenvia channel (https://www.zenvia.com/)
    """

    code = 'ZV'
    category = ChannelType.Category.PHONE

    name = "Zenvia"

    claim_blurb = _("""If you are based in Brazil, you can purchase a short code from <a href="http://www.zenvia.com.br/">Zenvia</a> and connect it in a few simple steps.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 150

    attachment_support = False

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ['America/Sao_Paulo']

    def send(self, channel, msg, text):
        # Zenvia accepts messages via a GET
        # http://www.zenvia360.com.br/GatewayIntegration/msgSms.do?dispatch=send&account=temba&
        # code=abc123&to=5511996458779&msg=my message content&id=123&callbackOption=1
        payload = dict(dispatch='send',
                       account=channel.config['account'],
                       code=channel.config['code'],
                       msg=text,
                       to=msg.urn_path,
                       id=msg.id,
                       callbackOption=1)

        zenvia_url = "http://www.zenvia360.com.br/GatewayIntegration/msgSms.do"
        headers = http_headers(extra={'Content-Type': "text/html", 'Accept-Charset': 'ISO-8859-1'})

        event = HttpEvent('POST', zenvia_url, urlencode(payload))

        start = time.time()

        try:
            response = requests.get(zenvia_url, params=payload, headers=headers, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(u"Unable to send message: %s" % six.text_type(e),
                                event=event, start=start)

        if response.status_code != 200 and response.status_code != 201:
            raise SendException("Got non-200 response from API: %d" % response.status_code,
                                event=event, start=start)

        response_code = int(response.text[:3])

        if response_code != 0:
            raise Exception("Got non-zero response from Zenvia: %s" % response.text)

        Channel.success(channel, msg, WIRED, start, event=event)
