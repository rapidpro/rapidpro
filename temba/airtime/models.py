# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import hashlib
import json
import requests
import six
import time

from django.conf import settings
from django.db import models
from django.utils.encoding import force_bytes
from smartmin.models import SmartModel
from temba.channels.models import Channel
from temba.contacts.models import Contact, TEL_SCHEME
from temba.orgs.models import Org, TRANSFERTO_ACCOUNT_LOGIN, TRANSFERTO_AIRTIME_API_TOKEN, TRANSFERTO_ACCOUNT_CURRENCY
from temba.utils import get_country_code_by_name


class AirtimeTransfer(SmartModel):
    TRANSFERTO_AIRTIME_API_URL = 'https://airtime.transferto.com/cgi-bin/shop/topup'
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
        md5.update(force_bytes(login + token + key))
        md5 = md5.hexdigest()

        data = kwargs
        data.update(dict(login=login, key=key, md5=md5))

        response = requests.post(cls.TRANSFERTO_AIRTIME_API_URL, data)

        if airtime_obj is not None:
            airtime_obj.data += json.dumps(data, indent=2) + AirtimeTransfer.LOG_DIVIDER
            airtime_obj.response += response.text + AirtimeTransfer.LOG_DIVIDER
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
        config = self.org.config
        login = config.get(TRANSFERTO_ACCOUNT_LOGIN, '')
        token = config.get(TRANSFERTO_AIRTIME_API_TOKEN, '')

        return AirtimeTransfer.post_transferto_api_response(login, token, airtime_obj=self, **kwargs)

    @classmethod
    def trigger_airtime_event(cls, org, ruleset, contact, event):
        from temba.api.models import get_api_user
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

            config = org.config
            account_currency = config.get(TRANSFERTO_ACCOUNT_CURRENCY, '')
            if not account_currency:
                org.refresh_transferto_account_currency()
                config = org.config
                account_currency = config.get(TRANSFERTO_ACCOUNT_CURRENCY, '')

            action = 'msisdn_info'
            request_kwargs = dict(action=action, destination_msisdn=airtime.recipient, currency=account_currency,
                                  delivered_amount_info='1')
            response = airtime.get_transferto_response(**request_kwargs)
            content_json = AirtimeTransfer.parse_transferto_response(response.text)

            error_code = int(content_json.get('error_code', None))
            error_txt = content_json.get('error_txt', None)

            if error_code != 0:
                message = "Got non-zero error code (%d) from TransferTo with message (%s)" % (error_code, error_txt)
                airtime.status = AirtimeTransfer.FAILED
                raise Exception(message)

            country_name = content_json.get('country', '')
            country_code = get_country_code_by_name(country_name)
            country_config = ruleset.config.get(country_code, dict())
            amount = country_config.get('amount', 0)

            airtime.amount = amount

            product_list = content_json.get('product_list', [])
            if not isinstance(product_list, list):
                product_list = [product_list]

            skuid_list = content_json.get('skuid_list', [])
            if not isinstance(skuid_list, list):
                skuid_list = [skuid_list]

            local_info_value_list = content_json.get('local_info_value_list', [])
            if not isinstance(local_info_value_list, list):
                local_info_value_list = [local_info_value_list]

            product_local_value_map = dict(zip([float(elt) for elt in local_info_value_list], product_list))

            product_skuid_value_map = dict(zip(product_list, skuid_list))

            targeted_prices = [float(i) for i in local_info_value_list if float(i) <= float(amount)]

            denomination = None
            skuid = content_json.get('skuid', None)
            if targeted_prices:
                denomination_key = max(targeted_prices)
                denomination = product_local_value_map.get(denomination_key, None)
                skuid = product_skuid_value_map.get(denomination)

            elif skuid:
                minimum_local = content_json.get('open_range_minimum_amount_local_currency', 0)
                maximum_local = content_json.get('open_range_maximum_amount_local_currency', 0)
                minimum_acc_currency = content_json.get('open_range_minimum_amount_requested_currency', 0)
                maximum_acc_currency = content_json.get('open_range_maximum_amount_requested_currency', 0)

                local_interval = float(maximum_local) - float(minimum_local)
                acc_currency_interval = float(maximum_acc_currency) - float(minimum_acc_currency)
                amount_interval = float(amount) - float(minimum_local)
                amount_interval_acc_currency = ((amount_interval * acc_currency_interval) / local_interval)
                denomination = str(float(minimum_acc_currency) + amount_interval_acc_currency)

            if denomination is not None:
                airtime.denomination = denomination

            if float(amount) <= 0:
                message = "Failed by invalid amount configuration or missing amount configuration for %s" % country_name
                airtime.status = AirtimeTransfer.FAILED
                raise Exception(message)

            if denomination is None:  # pragma: needs cover
                message = "No TransferTo denomination matched"
                airtime.status = AirtimeTransfer.FAILED
                raise Exception(message)

            action = 'reserve_id'
            request_kwargs = dict(action=action)
            response = airtime.get_transferto_response(**request_kwargs)
            content_json = AirtimeTransfer.parse_transferto_response(response.text)

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
                                  currency=account_currency,
                                  product=airtime.denomination)

            if skuid:
                request_kwargs['skuid'] = skuid

            response = airtime.get_transferto_response(**request_kwargs)
            content_json = AirtimeTransfer.parse_transferto_response(response.text)

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
            message = "Error transferring airtime: %s" % six.text_type(e)

        finally:
            airtime.message = message
            airtime.save()

        return airtime
