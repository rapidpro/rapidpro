# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import requests

from django.http import HttpResponse, JsonResponse
from django.utils.translation import ugettext_lazy as _
from django.views.generic import View
from six.moves.urllib.parse import parse_qs
from smartmin.views import SmartTemplateView, SmartReadView, SmartListView, SmartView
from temba.channels.models import ChannelEvent
from temba.orgs.views import OrgPermsMixin
from .models import WebHookEvent, APIToken, Resthook


class RefreshAPITokenView(OrgPermsMixin, SmartView, View):
    """
    Simple view that refreshes the API token for the user/org when POSTed to
    """
    permission = 'api.apitoken_refresh'

    def post(self, request, *args, **kwargs):
        token = APIToken.get_or_create(request.user.get_org(), request.user, refresh=True)
        return JsonResponse(dict(token=token.key))


class WebHookEventMixin(OrgPermsMixin):  # pragma: needs cover
    def get_status(self, obj):
        return obj.get_status_display()

    def get_tries(self, obj):
        return obj.try_count

    def derive_queryset(self, **kwargs):
        org = self.derive_org()
        return WebHookEvent.objects.filter(org=org)


class WebHookEventListView(WebHookEventMixin, SmartListView):
    model = WebHookEvent
    fields = ('event', 'status', 'channel', 'tries', 'created_on')
    title = _("Recent WebHook Events")
    template_name = 'api/webhookevent_list.html'
    default_order = ('-created_on',)
    permission = 'api.webhookevent_list'

    def get_context_data(self, *args, **kwargs):  # pragma: needs cover
        context = super(WebHookEventListView, self).get_context_data(*args, **kwargs)
        context['org'] = self.request.user.get_org()
        return context


class WebHookEventReadView(WebHookEventMixin, SmartReadView):
    model = WebHookEvent
    fields = ('event', 'status', 'channel', 'tries', 'next_attempt')
    template_name = 'api/webhookevent_read.html'
    permission = 'api.webhookevent_read'
    field_config = {'next_attempt': dict(label=_("Next Delivery")), 'tries': dict(label=_("Attempts"))}

    def get_next_attempt(self, obj):  # pragma: no cover
        if obj.next_attempt:
            return _("Around %s") % obj.next_attempt
        else:
            if obj.try_count == 3:
                return _("Never, three attempts errored, failed permanently")
            else:
                if obj.status == 'C':
                    return _("Never, event delivered successfully")
                else:
                    return _("Never, event delivery failed permanently")

    def get_context_data(self, *args, **kwargs):  # pragma: needs cover
        context = super(WebHookEventReadView, self).get_context_data(*args, **kwargs)

        context['results'] = self.object.results.all()
        return context


class WebHookTunnelView(View):
    http_method_names = ['post']

    def post(self, request):
        try:
            if 'url' not in request.POST or 'data' not in request.POST:
                return HttpResponse(_("Must include both 'url' and 'data' parameters."), status=400)

            url = request.POST['url']
            data = request.POST['data']

            # as a very rudimentary security measure we only pass down variables we know are valid
            incoming_data = parse_qs(data)
            outgoing_data = dict()
            for key in incoming_data.keys():
                if key in ['relayer', 'channel', 'sms', 'phone', 'text', 'time', 'call', 'duration', 'power_level', 'power_status',
                           'power_source', 'network_type', 'pending_message_count', 'retry_message_count', 'last_seen', 'event',
                           'step', 'values', 'flow', 'relayer_phone']:
                    outgoing_data[key] = incoming_data[key]

            response = requests.post(url, data=outgoing_data, timeout=3)
            result = response.text

        except Exception as e:  # pragma: needs cover
            result = str(e)

        return HttpResponse(result)


class WebHookView(SmartTemplateView):
    template_name = "api/webhook.html"


class WebHookSimulatorView(SmartTemplateView):
    template_name = "api/webhook_simulator.html"

    def get_context_data(self, **kwargs):
        context = super(WebHookSimulatorView, self).get_context_data(**kwargs)

        endpoints = list()

        fields = list()
        fields.append(dict(name="relayer", help="The id of the channel which received an SMS", default=5))
        fields.append(dict(name="relayer_phone", help="The phone number of the channel which received an SMS", default="+250788123123"))
        fields.append(dict(name="sms", help="The id of the incoming SMS message", default=1))
        fields.append(dict(name="phone", help="The phone number of the sender in E164 format", default="+250788123123"))
        fields.append(dict(name="text", help="The text of the SMS message", default="That gucci is hella tight"))
        fields.append(dict(name="status", help="The status of this SMS message, one of P,H,S,D,E,F", default="D"))
        fields.append(dict(name="direction", help="The direction of the SMS, either I for incoming or O for outgoing", default="I"))
        fields.append(dict(name="time", help="When this event occurred in ECMA-162 format", default="2013-01-21T22:34:00.123"))

        mo_sms = dict(event="mo_sms", title="Sent when your channel receives a new SMS message", fields=fields, color='green')
        mt_sent = dict(event="mt_sent", title="Sent when your channel has confirmed it has sent an outgoing SMS", fields=fields, color='green')
        mt_dlvd = dict(event="mt_dlvd", title="Sent when your channel receives a delivery report for an outgoing SMS", fields=fields, color='green')

        endpoints.append(mo_sms)
        endpoints.append(mt_sent)
        endpoints.append(mt_dlvd)

        fields = list()
        fields.append(dict(name="relayer", help="The id of the channel which received a call", default=5))
        fields.append(dict(name="relayer_phone", help="The phone number of the channel which received an SMS", default="+250788123123"))
        fields.append(dict(name="call", help="The id of the call", default=1))
        fields.append(dict(name="phone", help="The phone number of the caller or callee in E164 format", default="+250788123123"))
        fields.append(dict(name="duration", help="The duration of the call (always 0 for missed calls)", default="0"))
        fields.append(dict(name="time", help="When this event was received by the channel in ECMA-162 format", default="2013-01-21T22:34:00.123"))

        mo_call = dict(event=ChannelEvent.TYPE_CALL_IN, title="Sent when your channel receives an incoming call that was picked up", fields=fields, color='blue')
        mo_miss = dict(event=ChannelEvent.TYPE_CALL_IN_MISSED, title="Sent when your channel receives an incoming call that was missed", fields=fields, color='blue')
        mt_call = dict(event=ChannelEvent.TYPE_CALL_OUT, title="Sent when your channel places an outgoing call that was connected", fields=fields, color='blue')
        mt_miss = dict(event=ChannelEvent.TYPE_CALL_OUT_MISSED, title="Sent when your channel places an outgoing call that was not connected", fields=fields, color='blue')

        endpoints.append(mo_call)
        endpoints.append(mo_miss)
        endpoints.append(mt_call)
        endpoints.append(mt_miss)

        fields = list()
        fields.append(dict(name="relayer", help="The id of the channel which this alarm is for", default=1))
        fields.append(dict(name="relayer_phone", help="The phone number of the channel", default="+250788123123"))
        fields.append(dict(name="power_level", help="The current power level of the channel", default=65))
        fields.append(dict(name="power_status", help="The current power status, either CHARGING or DISCHARGING", default="CHARGING"))
        fields.append(dict(name="power_source", help="The source of power, ex: BATTERY, AC, USB", default="AC"))
        fields.append(dict(name="network_type", help="The type of network the device is connected to. ex: WIFI", default="WIFI"))
        fields.append(dict(name="pending_message_count", help="The number of unsent messages for this channel", default=0))
        fields.append(dict(name="retry_message_count", help="The number of messages that had send errors and are being retried", default=0))
        fields.append(dict(name="last_seen", help="The time that this channel last synced in ECMA-162 format", default="2013-01-21T22:34:00.123"))

        alarm = dict(event="alarm", title="Sent when we detects either a low battery, unsent messages, or lack of connectivity for your channel", fields=fields, color='red')

        endpoints.append(alarm)

        fields = list()
        fields.append(dict(name="relayer", help="The id of the channel which handled this flow step", default=1))
        fields.append(dict(name="relayer_phone", help="The phone number of the channel", default="+250788123123"))
        fields.append(dict(name="phone", help="The phone number of the contact", default="+250788788123"))
        fields.append(dict(name="flow", help="The id of the flow (reference the URL on your flow page)", default=504))
        fields.append(dict(name="step", help="The uuid of the step which triggered this event (reference your flow)", default="15121251-15121241-15145152-12541241"))
        fields.append(dict(name="time", help="The time that this step was reached by the user in ECMA-162 format", default="2013-01-21T22:34:00.123"))
        fields.append(dict(name="values", help="The values that have been collected for this contact thus far through the flow",
                           default='[{ "label": "Water Source", "category": "Stream", "text": "from stream", "time": "2013-01-01T05:35:32.012" },'
                                   ' { "label": "Boil", "category": "Yes", "text": "yego", "time": "2013-01-01T05:36:54.012" }]'))

        flow = dict(event="flow", title="Sent when a user reaches an API node in a flow", fields=fields, color='purple')

        endpoints.append(flow)

        context['endpoints'] = endpoints
        return context


class ResthookList(OrgPermsMixin, SmartListView):
    model = Resthook
    permission = 'api.resthook_list'

    def derive_queryset(self):
        return Resthook.objects.filter(is_active=True, org=self.request.user.get_org()).order_by('slug')
