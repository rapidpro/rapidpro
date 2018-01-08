from __future__ import unicode_literals, absolute_import

import time
import urlparse
import requests
import six

from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _

from temba.channels.types.yo.views import ClaimView
from temba.contacts.models import Contact, TEL_SCHEME
from temba.msgs.models import SENT
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException

YO_API_URL_1 = 'http://smgw1.yo.co.ug:9100/sendsms'
YO_API_URL_2 = 'http://41.220.12.201:9100/sendsms'
YO_API_URL_3 = 'http://164.40.148.210:9100/sendsms'


class YoType(ChannelType):
    """
    An Yo! channel (http://www.yo.co.ug/)
    """

    code = 'YO'
    category = ChannelType.Category.PHONE

    name = "YO!"
    slug = 'yo'

    claim_blurb = _("""If you are based in Uganda, you can integrate with <a href="http://www.yo.co.ug/">Yo!</a> to send
                    and receive messages on your shortcode.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Africa/Kampala"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)

    def send(self, channel, msg, text):

        # build our message dict
        params = dict(origin=channel.address.lstrip('+'),
                      sms_content=text,
                      destinations=msg.urn_path.lstrip('+'),
                      ybsacctno=channel.config['username'],
                      password=channel.config['password'])
        log_params = params.copy()
        log_params['password'] = 'x' * len(log_params['password'])

        start = time.time()
        failed = False
        fatal = False
        events = []

        for send_url in [YO_API_URL_1, YO_API_URL_2, YO_API_URL_3]:
            url = send_url + '?' + urlencode(params)
            log_url = send_url + '?' + urlencode(log_params)

            event = HttpEvent('GET', log_url)
            events.append(event)

            failed = False
            try:
                response = requests.get(url, headers=http_headers(), timeout=5)
                event.status_code = response.status_code
                event.response_body = response.text

                response_qs = urlparse.parse_qs(response.text)
            except Exception:
                failed = True

            if not failed and response.status_code != 200 and response.status_code != 201:
                failed = True

            # if it wasn't successfully delivered, throw
            if not failed and response_qs.get('ybs_autocreate_status', [''])[0] != 'OK':
                failed = True

            # check if we failed permanently (they blocked us)
            if failed and response_qs.get('ybs_autocreate_message', [''])[0].find('BLACKLISTED') >= 0:
                contact = Contact.objects.get(id=msg.contact)
                contact.stop(contact.modified_by)
                fatal = True
                break

            # if we sent the message, then move on
            if not failed:
                break

        if failed:
            raise SendException("Received error from Yo! API",
                                events=events, fatal=fatal, start=start)

        Channel.success(channel, msg, SENT, start, events=events)
