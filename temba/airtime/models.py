import hashlib
import json
import time

import requests
from django.conf import settings
from django.db import models


from smartmin.models import SmartModel
from temba.api.models import get_api_user
from temba.channels.models import Channel
from temba.contacts.models import Contact, TEL_SCHEME
from temba.orgs.models import Org, TRANSFERTO_ACCOUNT_LOGIN, TRANSFERTO_AIRTIME_API_TOKEN
from temba.utils import get_country_code_by_name


class AirtimeTransfer(SmartModel):
    TRANSFERTO_AIRTIME_API_URL = 'https://fm.transfer-to.com/cgi-bin/shop/topup'
    LOG_DIVIDER = "\n\n%s\n\n" % ('=' * 20)

    PENDING = 'P'
    SUCCESS = 'S'
    FAILED = 'F'

    STATUS_CHOICES = ((PENDING, "Pending"),
                      (SUCCESS, "Success"),
                      (FAILED, "Failed"))

    org = models.ForeignKey(Org, help_text="The organization that this airtime was triggered for")

    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default='P',
                              help_text="The state this event is currently in")

    channel = models.ForeignKey(Channel, null=True, blank=True,
                                help_text="The channel that this airtime is relating to")

    contact = models.ForeignKey(Contact, help_text="The contact that this airtime is sent to")

    recipient = models.CharField(max_length=64)

    amount = models.FloatField()

    denomination = models.CharField(max_length=32, null=True, blank=True)

    data = models.TextField(null=True, blank=True, default="")

    response = models.TextField(null=True, blank=True, default="")

    message = models.CharField(max_length=255, null=True, blank=True,
                               help_text="A message describing the end status, error messages go here")

    @classmethod
    def post_transferto_api_response(cls, login, token, airtime_obj=None, **kwargs):
        if not settings.SEND_AIRTIME:
            raise Exception("!! Skipping Airtime Transfer, SEND_AIRTIME set to False")

        key = str(int(time.time()))
        md5 = hashlib.md5()
        md5.update(login + token + key)
        md5 = md5.hexdigest()

        data = kwargs
        data.update(dict(login=login, key=key, md5=md5))

        response = requests.post(cls.TRANSFERTO_AIRTIME_API_URL, data)

        if airtime_obj is not None:
            airtime_obj.data += json.dumps(data, indent=2) + AirtimeTransfer.LOG_DIVIDER
            airtime_obj.response += response.content + AirtimeTransfer.LOG_DIVIDER
            airtime_obj.save()

        return response

    @classmethod
    def parse_transferto_response(cls, content):
        splitted_content = content.split('\r\n')
        parsed = dict()

        for elt in splitted_content:
            if elt and elt.find('=') > 0:
                key, val = tuple(elt.split('='))
                if val.find(',') > 0:
                    val = val.split(',')

                parsed[key] = val

        return parsed

    def get_transferto_response(self, **kwargs):
        config = self.org.config_json()
        login = config.get(TRANSFERTO_ACCOUNT_LOGIN, '')
        token = config.get(TRANSFERTO_AIRTIME_API_TOKEN, '')

        return AirtimeTransfer.post_transferto_api_response(login, token, airtime_obj=self, **kwargs)

    @classmethod
    def trigger_airtime_event(cls, org, ruleset, contact, event):

        api_user = get_api_user()

        channel = None
        contact_urn = None
        # if we have an SMS event use its channel and contact urn
        if event:
            channel = event.channel
            contact_urn = event.contact_urn

        if not contact_urn:
            contact_urn = contact.get_urn(TEL_SCHEME)

        airtime = AirtimeTransfer.objects.create(org=org, channel=channel, contact=contact, recipient=contact_urn.path,
                                                 amount=0, created_by=api_user, modified_by=api_user)

        message = "None"
        try:

            if not org.is_connected_to_transferto():
                message = "No transferTo Account connected to this organization"
                airtime.status = AirtimeTransfer.FAILED
                raise Exception(message)

            action = 'msisdn_info'
            request_kwargs = dict(action=action, destination_msisdn=airtime.recipient)
            response = airtime.get_transferto_response(**request_kwargs)
            content_json = AirtimeTransfer.parse_transferto_response(response.content)

            error_code = int(content_json.get('error_code', None))
            error_txt = content_json.get('error_txt', None)

            if error_code != 0:
                message = "Got non-zero error code (%d) from TransferTo with message (%s)" % (error_code, error_txt)
                airtime.status = AirtimeTransfer.FAILED
                raise Exception(message)

            country_name = content_json.get('country', '')
            country_code = get_country_code_by_name(country_name)
            amount_config = ruleset.config_json()
            country_config = amount_config.get(country_code, dict())
            amount = country_config.get('amount', 0)

            airtime.amount = amount

            product_list = content_json.get('product_list', [])

            if not isinstance(product_list, list):
                product_list = [product_list]

            targeted_prices = [float(i) for i in product_list if float(i) <= float(amount)]

            denomination = None
            if targeted_prices:
                denomination = max(targeted_prices)
                airtime.denomination = denomination

            if float(amount) <= 0:
                message = "Failed by invalid amount configuration or missing amount configuration for %s" % country_name
                airtime.status = AirtimeTransfer.FAILED
                raise Exception(message)

            if denomination is None:
                message = "No TransferTo denomination matched"
                airtime.status = AirtimeTransfer.FAILED
                raise Exception(message)

            action = 'reserve_id'
            request_kwargs = dict(action=action)
            response = airtime.get_transferto_response(**request_kwargs)
            content_json = AirtimeTransfer.parse_transferto_response(response.content)

            error_code = int(content_json.get('error_code', None))
            error_txt = content_json.get('error_txt', None)

            if error_code != 0:
                message = "Got non-zero error code (%d) from TransferTo with message (%s)" % (error_code, error_txt)
                airtime.status = AirtimeTransfer.FAILED
                raise Exception(message)

            transaction_id = content_json.get('reserved_id')

            action = 'topup'
            request_kwargs = dict(action=action,
                                  reserved_id=transaction_id,
                                  msisdn=channel.address if channel else '',
                                  destination_msisdn=airtime.recipient,
                                  product=airtime.denomination)
            response = airtime.get_transferto_response(**request_kwargs)
            content_json = AirtimeTransfer.parse_transferto_response(response.content)

            error_code = int(content_json.get('error_code', None))
            error_txt = content_json.get('error_txt', None)

            if error_code != 0:
                message = "Got non-zero error code (%d) from TransferTo with message (%s)" % (error_code, error_txt)
                airtime.status = AirtimeTransfer.FAILED
                raise Exception(message)

            message = "Airtime Transferred Successfully"
            airtime.status = AirtimeTransfer.SUCCESS

        except Exception as e:
            import traceback
            traceback.print_exc()

            airtime.status = AirtimeTransfer.FAILED
            message = "Error transferring airtime: %s" % unicode(e)

        finally:
            airtime.message = message
            airtime.save()

        return airtime
