from __future__ import absolute_import, unicode_literals

import json
import pytz
import requests
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.views.generic import View
from django.views.generic.list import MultipleObjectMixin
from redis_cache import get_redis_connection
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.mixins import DestroyModelMixin
from rest_framework.reverse import reverse
from rest_framework.response import Response
from rest_framework.permissions import BasePermission, IsAuthenticated
from smartmin.views import SmartTemplateView, SmartReadView, SmartListView
from temba.api.models import WebHookEvent, WebHookResult, SMS_RECEIVED
from temba.api.serializers import BoundarySerializer, BroadcastReadSerializer, CallSerializer, CampaignSerializer
from temba.api.serializers import CampaignWriteSerializer, CampaignEventSerializer, CampaignEventWriteSerializer
from temba.api.serializers import ContactGroupReadSerializer, ContactReadSerializer, ContactWriteSerializer
from temba.api.serializers import ContactFieldReadSerializer, ContactFieldWriteSerializer, BroadcastCreateSerializer
from temba.api.serializers import FlowReadSerializer, FlowRunReadSerializer, FlowRunStartSerializer, FlowWriteSerializer
from temba.api.serializers import MsgCreateSerializer, MsgCreateResultSerializer, MsgReadSerializer, MsgBulkActionSerializer
from temba.api.serializers import LabelReadSerializer, LabelWriteSerializer
from temba.api.serializers import ChannelClaimSerializer, ChannelReadSerializer, ResultSerializer
from temba.assets.models import AssetType
from temba.assets.views import handle_asset_request
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel, PLIVO
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, TEL_SCHEME, USER_DEFINED_GROUP
from temba.flows.models import Flow, FlowRun, RuleSet
from temba.locations.models import AdminBoundary
from temba.orgs.models import get_stripe_credentials, NEXMO_UUID
from temba.orgs.views import OrgPermsMixin
from temba.msgs.models import Broadcast, Msg, Call, Label, HANDLE_EVENT_TASK, HANDLER_QUEUE, MSG_EVENT
from temba.triggers.models import Trigger, MISSED_CALL_TRIGGER
from temba.utils import analytics, json_date_to_datetime, JsonResponse, splitting_getlist, str_to_bool
from temba.utils.middleware import disable_middleware
from temba.utils.queues import push_task
from twilio import twiml
from urlparse import parse_qs



def webhook_status_processor(request):
    status = dict()
    user = request.user
    
    if user.is_superuser or user.is_anonymous():
        return status
        
    # get user's org
    org = user.get_org()
    
    if org:
        past_hour = timezone.now() - timedelta(hours=1)
        failed = WebHookEvent.objects.filter(org=org, status__in=['F','E'], created_on__gte=past_hour).order_by('-created_on')

        if failed:
            status['failed_webhooks'] = True
            status['webhook_errors_count'] = failed.count()

    return status

class ApiPermission(BasePermission):
    def has_permission(self, request, view):
        if getattr(view, 'permission', None):
            if request.user.is_anonymous():
                return False

            org = request.user.get_org()
            if org:
                group = org.get_user_org_group(request.user)
                codename = view.permission.split(".")[-1]
                return group.permissions.filter(codename=codename)
            else:
                return False
        else: # pragma: no cover
            return True


class SSLPermission(BasePermission): # pragma: no cover
    def has_permission(self, request, view):
        if getattr(settings, 'SESSION_COOKIE_SECURE', False):
            return request.is_secure()
        else:
            return True

class WebHookEventMixin(OrgPermsMixin):
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
    title = "Recent WebHook Events"
    template_name = 'api/webhookevent_list.html'
    default_order = ('-created_on',)
    permission = 'api.webhookevent_list'

    def get_context_data(self, *args, **kwargs):
        context = super(WebHookEventListView, self).get_context_data(*args, **kwargs)
        context['org'] = self.request.user.get_org()
        return context

class WebHookEventReadView(WebHookEventMixin, SmartReadView):
    model = WebHookEvent
    fields = ('event', 'status', 'channel', 'tries', 'next_attempt')
    template_name = 'api/webhookevent_read.html'
    permission = 'api.webhookevent_read'
    field_config = { 'next_attempt': dict(label="Next Delivery"), 'tries': dict(label="Attempts") }

    def get_next_attempt(self, obj): # pragma: no cover
        if obj.next_attempt:
            return "Around %s" % obj.next_attempt
        else:
            if obj.try_count == 3:
                return "Never, three attempts errored, failed permanently"
            else:
                if obj.status == 'C':
                    return "Never, event delivered successfully"
                else:
                    return "Never, event deliverey failed permanently"

    def get_context_data(self, *args, **kwargs):
        context = super(WebHookEventReadView, self).get_context_data(*args, **kwargs)

        context['results'] = WebHookResult.objects.filter(event=self.object)
        return context


class WebHookTunnelView(View):
    http_method_names = ['post',]

    def post(self, request):
        try:
            if not 'url' in request.POST or not 'data' in request.POST:
                return HttpResponse("Must include both 'url' and 'data' parameters.", status=400)

            url = request.POST['url']
            data = request.POST['data']

            # as a very rudimentary security measure we only pass down variables we know are valid
            incoming_data = parse_qs(data)
            outgoing_data = dict()
            for key in incoming_data.keys():
                if key in ['relayer', 'sms', 'phone', 'text', 'time', 'call', 'duration', 'power_level', 'power_status',
                           'power_source', 'network_type', 'pending_message_count', 'retry_message_count', 'last_seen', 'event']:
                    outgoing_data[key] = incoming_data[key]

            response = requests.post(url, data=outgoing_data, timeout=3)
            result = response.text

        except Exception as e:
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

        mo_call = dict(event="mo_call", title="Sent when your channel receives an incoming call that was picked up", fields=fields, color='blue')
        mo_miss = dict(event="mo_miss", title="Sent when your channel receives an incoming call that was missed", fields=fields, color='blue')
        mt_call = dict(event="mt_call", title="Sent when your channel places an outgoing call that was connected", fields=fields, color='blue')
        mt_miss = dict(event="mt_miss", title="Sent when your channel places an outgoing call that was not connected", fields=fields, color='blue')

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


class ApiExplorerView(SmartTemplateView):
    template_name = "api/api_explorer.html"

    def get_context_data(self, **kwargs):
        context = super(ApiExplorerView, self).get_context_data(**kwargs)

        endpoints = list()
        endpoints.append(Channels.get_read_explorer())
        endpoints.append(Channels.get_write_explorer())
        endpoints.append(Channels.get_delete_explorer())

        endpoints.append(Contacts.get_read_explorer())
        endpoints.append(Contacts.get_write_explorer())
        endpoints.append(Contacts.get_delete_explorer())

        endpoints.append(Groups.get_read_explorer())

        endpoints.append(FieldsEndpoint.get_read_explorer())
        endpoints.append(FieldsEndpoint.get_write_explorer())

        endpoints.append(MessagesEndpoint.get_read_explorer())
        #endpoints.append(MessagesEndpoint.get_write_explorer())

        endpoints.append(MessagesBulkActionEndpoint.get_write_explorer())

        endpoints.append(BroadcastsEndpoint.get_read_explorer())
        endpoints.append(BroadcastsEndpoint.get_write_explorer())

        endpoints.append(LabelsEndpoint.get_read_explorer())
        endpoints.append(LabelsEndpoint.get_write_explorer())

        endpoints.append(BroadcastsEndpoint.get_read_explorer())
        endpoints.append(BroadcastsEndpoint.get_write_explorer())

        endpoints.append(Calls.get_read_explorer())

        endpoints.append(FlowEndpoint.get_read_explorer())

        endpoints.append(FlowRunEndpoint.get_read_explorer())
        endpoints.append(FlowRunEndpoint.get_write_explorer())

        #endpoints.append(FlowResultsEndpoint.get_read_explorer())

        endpoints.append(CampaignEndpoint.get_read_explorer())
        endpoints.append(CampaignEndpoint.get_write_explorer())

        endpoints.append(CampaignEventEndpoint.get_read_explorer())
        endpoints.append(CampaignEventEndpoint.get_write_explorer())
        endpoints.append(CampaignEventEndpoint.get_delete_explorer())

        endpoints.append(BoundaryEndpoint.get_read_explorer())

        context['endpoints'] = endpoints

        from temba.settings import API_URL
        context['API_URL'] = API_URL

        return context

@api_view(['GET'])
@permission_classes((SSLPermission, IsAuthenticated))
def api(request, format=None):
    """
    ## REST API

    We provides a simple REST API for you to interact with your data from outside applications.

    All endpoints should be accessed using HTTPS. The following endpoints are provided:

     * [/api/v1/contacts](/api/v1/contacts) - To list or modify contacts.
     * [/api/v1/fields](/api/v1/fields) - To list or modify contact fields.
     * [/api/v1/messages](/api/v1/messages) - To list messages.
     * [/api/v1/labels](/api/v1/labels) - To list and create new message labels.
     * [/api/v1/broadcasts](/api/v1/broadcasts) - To list and create outbox broadcasts.
     * [/api/v1/relayers](/api/v1/relayers) - To list, create and remove new Android phones.
     * [/api/v1/calls](/api/v1/calls) - To list incoming, outgoing and missed calls as reported by the Android phone.
     * [/api/v1/flows](/api/v1/flows) - To list active flows
     * [/api/v1/runs](/api/v1/runs) - To list or start flow runs for contacts
     * [/api/v1/campaigns](/api/v1/campaigns) - To list or modify campaigns on your account.
     * [/api/v1/events](/api/v1/events) - To list or modify campaign events on your account.
     * [/api/v1/boundaries](/api/v1/boundaries) - To retrieve the geometries of the administrative boundaries on your account.

    You may wish to use the [API Explorer](/api/v1/explorer) to interactively experiment with API.

    ## Web Hook

    Your application can be notified when new messages are received, sent or delivered.  You can
    configure a URL for those events to be delivered to.  Visit the [Web Hook Documentation](/api/v1/webhook/) and
    [Simulator](/api/v1/webhook/simulator/) for more details.

    ## Verbs

    All API calls follow standard REST conventions.  You can list a set of resources by making a **GET** request on the endpoint
    and either send new messages or claim channels using the **POST** verb.  You can receive responses either
    in JSON or XML by appending the corresponding extension to the endpoint URL, ex: ```/api/v1.json```

    ## Status Codes

    The success or failure of requests is represented by status codes as well as a verbose message in the response body:

    * **200**: A read operation was successful
    * **201**: A resource was successfully created (only returned for POST methods)
    * **204**: A resource was successfully deleted (only returned for DELETE methods)
    * **400**: The request failed due to invalid parameters, do not retry with the same values, the body of the response
               will contain details
    * **403**: You do not have permission to access this resource
    * **404**: The resource was not found (currently only returned by DELETE methods)

    ## Data Types

    The following data types are used in our JSON API:

    * **string**: A standard JSON string
    * **int**: Unsigned 32 bit integer
    * **datetime**: A date/time in [ECMA-162 format](http://ecma-international.org/ecma-262/5.1/#sec-15.9.1.15): YYYY-MM-DDTHH:mm:ss.sssZ ex: ```2013-03-02T17:28:12.084```

    ## Phone Numbers

    Phone numbers are represented as JSON strings in the [E164 format](http://en.wikipedia.org/wiki/E.164)  ex: ```+250788123123```

    Note that we can't know all legal phone numbers, so while it does try to normalize numbers to an international
    format when possible, it will never reject a value for a phone number.  It is recommended to make sure the phone
    numbers you pass are always in full E164 format.

    ## Filtering

    All pages that return a list of items support filtering by one or more attributes. You define how you want the list
    filtered via request parameters.  Note that when filtering by phone number you will need to use the E164 format
    and URL encode the + character as %2B. An example to retrieve all the outgoing messages since January 1st, 2013
    that are in a state of Q or S for the number +250788123123:

        /api/v1/messages.json?after=2013-01-01T00:00:00.000&status=Q,S&direction=O&urn=tel:%2B250788123123

    ## Authentication

    You must authenticate all calls by including an ```Authorization``` header with your API token. For
    security reasons all calls must be made using HTTPS.

    You Authorization header should look like this:

        Authorization: Token YOUR_API_TOKEN_GOES_HERE

    **Note that all calls made through this web interface are against the live API, please exercise the appropriate caution.**
    """
    return Response({
        'boundaries': reverse('api.boundaries', request=request),
        'broadcasts': reverse('api.broadcasts', request=request),
        'calls': reverse('api.calls', request=request),
        'campaigns': reverse('api.campaigns', request=request),
        'contacts': reverse('api.contacts', request=request),
        'events': reverse('api.campaignevents', request=request),
        'fields': reverse('api.contactfields', request=request),
        'flows': reverse('api.flows', request=request),
        'labels': reverse('api.labels', request=request),
        'messages': reverse('api.messages', request=request),
        'relayers': reverse('api.channels', request=request),
        'runs': reverse('api.runs', request=request),
        'sms': reverse('api.sms', request=request),
    })


class BroadcastsEndpoint(generics.ListAPIView):
    """
    This endpoint allows you either list message broadcasts on your account using the ```GET``` method or create new
    message broadcasts using the ```POST``` method.

    ## Sending Messages

    You can create new broadcasts by making a **POST** request to this URL with the following JSON data:

      * **urns** - JSON array of URNs to send the message to (array of strings, optional)
      * **contacts** - JSON array of contact UUIDs to send the message to (array of strings, optional)
      * **groups** - JSON array of group UUIDs to send the message to (array of strings, optional)
      * **text** - the text of the message to send (string, limit of 480 characters)
      * **channel** - the id of the channel to use. Contacts and URNs which can't be reached with this channel are ignored (int, optional)

    Example:

        POST /api/v1/broadcasts.json
        {
            "urns": ["tel:+250788123123", "tel:+250788123124"],
            "contacts": ["09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"],
            "text": "hello world"
        }

    You will receive a response containing the message broadcast created:

        {
            "id": 1234,
            "urns": ["tel:+250788123123", "tel:+250788123124"],
            "contacts": ["09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"]
            "groups": [],
            "text": "hello world",
            "created_on": "2013-03-02T17:28:12",
            "status": "Q"
        }

    ## Listing Broadcasts

    Returns the message activity for your organization, listing the most recent messages first.

      * **id** - the id of the broadcast (int) (filterable: ```id``` repeatable)
      * **urns** - the contact URNs that received the broadcast (array of strings)
      * **contacts** - the UUIDs of contacts that received the broadcast (array of strings)
      * **groups** - the UUIDs of groups that received the broadcast (array of strings)
      * **text** - the text - note that the sent messages may have been received as multiple text messages (string)
      * **created_on** - the datetime when this message was either received by the channel or created (datetime) (filterable: ```before``` and ```after```)
      * **status** - the status of this broadcast, a string one of: (filterable: ```status``` repeatable)
            I - no messages have been sent yet
            Q - some messages are still queued
            S - all messages have been sent
            D - all messages have been delivered
            E - majority of messages have errored
            F - majority of messages have failed

    Example:

        GET /api/v1/broadcasts.json

    Response is a list of recent broadcasts:

        {
            "count": 15,
            "next": "/api/v1/broadcasts/?page=2",
            "previous": null,
            "results": [
                {
                    "id": 1234,
                    "urns": ["tel:+250788123123", "tel:+250788123124"],
                    "contacts": ["09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"]
                    "groups": [],
                    "text": "hello world",
                    "messages": [
                       158, 159
                    ],
                    "created_on": "2013-03-02T17:28:12",
                    "status": "Q"
                },
                ...

    """
    permission = 'msgs.broadcast_api'
    model = Broadcast
    permission_classes = (SSLPermission, ApiPermission)
    serializer_class = BroadcastReadSerializer
    form_serializer_class = BroadcastCreateSerializer

    def post(self, request, format=None):
        user = request.user
        serializer = BroadcastCreateSerializer(user=user, data=request.DATA)
        if serializer.is_valid():
            serializer.save()

            response_serializer = BroadcastReadSerializer(instance=serializer.object)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = self.model.objects.filter(org=self.request.user.get_org(), is_active=True).order_by('-created_on')

        ids = splitting_getlist(self.request, 'id')
        if ids:
            queryset = queryset.filter(pk__in=ids)

        statuses = splitting_getlist(self.request, 'status')
        if statuses:
            statuses = [status.upper() for status in statuses]
            queryset = queryset.filter(status__in=statuses)

        before = self.request.QUERY_PARAMS.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except:
                queryset = queryset.filter(pk=-1)

        after = self.request.QUERY_PARAMS.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except:
                queryset = queryset.filter(pk=-1)

        return queryset.order_by('-created_on').prefetch_related('urns', 'contacts', 'groups')

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List recent broadcasts",
                    url=reverse('api.broadcasts'),
                    slug='broadcast-list',
                    request="after=2013-01-01T00:00:00.000&status=Q,S")
        spec['fields'] = [dict(name='id', required=False,
                               help="One or more message ids to filter by (repeatable).  ex: 234235,230420"),
                          dict(name='status', required=False,
                               help="One or more status states to filter by (repeatable).  ex: Q,S,D"),
                          dict(name='before', required=False,
                               help="Only return messages before this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='after', required=False,
                               help="Only return messages after this date.  ex: 2012-01-28T18:00:00.000")]
        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Send one or more messages",
                    url=reverse('api.broadcasts'),
                    slug='broadcast-send',
                    request='{ "urns": ["tel:+250788222222", "tel:+250788111111"], "text": "My first message" }')

        spec['fields'] = [dict(name='urns', required=False,
                               help="A JSON array of one or more strings, each a contact URN."),
                          dict(name='contacts', required=False,
                               help="A JSON array of one or more strings, each a contact UUID."),
                          dict(name='groups', required=False,
                               help="A JSON array of one or more strings, each a group UUID."),
                          dict(name='text', required=True,
                               help="The text of the message you want to send (max length 480 chars)"),
                          dict(name='channel', required=False,
                               help="The id of the channel to use for sending")]
        return spec


class MessagesEndpoint(generics.ListAPIView):
    """
    This endpoint allows you either list messages on your account using the ```GET``` method or send new messages
    using the ```POST``` method.

    ## Sending Messages

    ** Note that sending messages using this endpoint is deprecated, you should instead use the Broadcasts endpoint to
       send new messages **

    You can create new messages by making a **POST** request to this URL with the following JSON data:

      * **channel** - the id of the channel that should send the messages (int, optional)
      * **urn** - either a single URN or a JSON array of up to 100 urns to send the message to (string or array of strings)
      * **contact** - either a single contact UUID or a JSON array of up to 100 contact UUIDs to send the message to (string or array of strings)
      * **text** - the text of the message to send (string, limit of 480 characters)

    Example:

        POST /api/v1/messages.json
        {
            "urns": ["tel:+250788123123", "tel:+250788123124"],
            "text": "hello world"
        }

    You will receive a response containing the ids of the messages created:

        {
            "messages": [
               158, 159
            ]
        }

    ## Listing Messages

    Returns the message activity for your organization, listing the most recent messages first.

      * **channel** - the id of the channel that sent or received this message (int) (filterable: ```channel``` repeatable)
      * **urn** - the URN of the sender or receiver, depending on direction (string) (filterable: ```urn``` repeatable)
      * **contact** - the UUID of the contact (string) (filterable: ```contact```repeatable )
      * **group_uuids** - the UUIDs of any groups the contact belongs to (string) (filterable: ```group_uuids``` repeatable)
      * **direction** - the direction of the SMS, either ```I``` for incoming messages or ```O``` for outgoing (string) (filterable: ```direction``` repeatable)
      * **labels** - Any labels set on this message (filterable: ```label``` repeatable)
      * **text** - the text of the message received, note this is the logical view, this message may have been received as multiple text messages (string)
      * **created_on** - the datetime when this message was either received by the channel or created (datetime) (filterable: ```before``` and ```after```)
      * **sent_on** - for outgoing messages, the datetime when the channel sent the message (null if not yet sent or an incoming message) (datetime)
      * **delivered_on** - for outgoing messages, the datetime when the channel delivered the message (null if not yet sent or an incoming message) (datetime)
      * **flow** - the flow this message is associated with (only filterable as ```flow``` repeatable)
      * **broadcast** - the broadcast this message is associated with (only filterable as ```broadcast``` repeatable)
      * **status** - the status of this message, a string one of: (filterable: ```status``` repeatable)

            Q - Message is queued awaiting to be sent
            S - Message has been sent by the channel
            D - Message was delivered to the recipient
            H - Incoming message was handled
            F - Message was not sent due to a failure

      * **type** - the type of the message, a string one of: (filterable: ```type``` repeatable)

            I - A message that was either sent or received from the message inbox
            F - A message that was either sent or received by a flow

    Example:

        GET /api/v1/messages.json

    Response is a list of recent messages:

        {
            "count": 389,
            "next": "/api/v1/messages/?page=2",
            "previous": null,
            "results": [
            {
                "id": 159,
                "contact": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
                "status": "Q",
                "relayer": 5,
                "urn": "tel:+250788123124",
                "direction": "O",
                "text": "hello world",
                "created_on": "2013-03-02T17:28:12",
                "sent_on": null,
                "delivered_on": null
            },
            ...

    """
    permission = 'msgs.msg_api'
    model = Msg
    permission_classes = (SSLPermission, ApiPermission)
    serializer_class = MsgReadSerializer
    form_serializer_class = MsgCreateSerializer

    def post(self, request, format=None):
        user = request.user
        serializer = MsgCreateSerializer(user=user, data=request.DATA)
        if serializer.is_valid():
            serializer.save()

            response_serializer = MsgCreateResultSerializer(instance=serializer.object)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = Msg.objects.filter(org=self.request.user.get_org())

        ids = splitting_getlist(self.request, 'id')
        if ids:
            queryset = queryset.filter(pk__in=ids)

        smses = splitting_getlist(self.request, 'sms')  # deprecated, use id
        if smses:
            queryset = queryset.filter(pk__in=smses)

        statuses = splitting_getlist(self.request, 'status')
        if statuses:
            statuses = [status.upper() for status in statuses]

            # to the user, Q and P are the same, but not to us
            if 'Q' in statuses:
                statuses.append('P')
            queryset = queryset.filter(status__in=statuses)

        directions = splitting_getlist(self.request, 'direction')
        if directions:
            queryset = queryset.filter(direction__in=directions)

        phones = splitting_getlist(self.request, 'phone')
        if phones:
            queryset = queryset.filter(contact__urns__path__in=phones)

        urns = self.request.QUERY_PARAMS.getlist('urn', None)
        if urns:
            queryset = queryset.filter(contact__urns__urn__in=urns)

        before = self.request.QUERY_PARAMS.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except:
                queryset = queryset.filter(pk=-1)

        after = self.request.QUERY_PARAMS.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except:
                queryset = queryset.filter(pk=-1)

        channels = self.request.QUERY_PARAMS.getlist('channel', None)
        if channels:
            queryset = queryset.filter(channel__id__in=channels)

        contact_uuids = splitting_getlist(self.request, 'contact')
        if contact_uuids:
            queryset = queryset.filter(contact__uuid__in=contact_uuids)

        groups = self.request.QUERY_PARAMS.getlist('group', None)  # deprecated, use group_uuids
        if groups:
            queryset = queryset.filter(contact__all_groups__name__in=groups,
                                       contact__all_groups__group_type=USER_DEFINED_GROUP)

        group_uuids = splitting_getlist(self.request, 'group_uuids')
        if group_uuids:
            queryset = queryset.filter(contact__all_groups__uuid__in=group_uuids,
                                       contact__all_groups__group_type=USER_DEFINED_GROUP)

        types = splitting_getlist(self.request, 'type')
        if types:
            queryset = queryset.filter(msg_type__in=types)

        labels = self.request.QUERY_PARAMS.getlist('label', None)
        if labels:
            queryset = queryset.filter(labels__name__in=labels)

        text = self.request.QUERY_PARAMS.get('text', None)
        if text:
            queryset = queryset.filter(text__icontains=text)

        flows = splitting_getlist(self.request, 'flow')
        if flows:
            queryset = queryset.filter(steps__run__flow__in=flows)

        broadcasts = splitting_getlist(self.request, 'broadcast')
        if broadcasts:
            queryset = queryset.filter(broadcast__in=broadcasts)

        reverse_order = self.request.QUERY_PARAMS.get('reverse', None)
        order = 'created_on' if reverse_order and str_to_bool(reverse_order) else '-created_on'

        return queryset.order_by(order).prefetch_related('labels').distinct()

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List recent messages",
                    url=reverse('api.messages'),
                    slug='sms-list',
                    request="after=2013-01-01T00:00:00.000&status=Q,S")
        spec['fields'] = [dict(name='id', required=False,
                               help="One or more message ids to filter by. (repeatable)  ex: 234235,230420"),
                          dict(name='contact', required=False,
                               help="One or more contact UUIDs to filter by. (repeatable)  ex: 09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"),
                          dict(name='group_uuids', required=False,
                               help="One or more contact group UUIDs to filter by. (repeatable) ex: 0ac92789-89d6-466a-9b11-95b0be73c683"),
                          dict(name='status', required=False,
                               help="One or more status states to filter by. (repeatable) ex: Q,S,D"),
                          dict(name='direction', required=False,
                               help="One or more directions to filter by. (repeatable) ex: I,O"),
                          dict(name='type', required=False,
                               help="One or more message types to filter by (inbox or flow). (repeatable) ex: I,F"),
                          dict(name='urn', required=False,
                               help="One or more URNs to filter messages by. (repeatable) ex: tel:+250788123123"),
                          dict(name='label', required=False,
                               help="One or more message labels to filter by. (repeatable) ex: Clogged Filter"),
                          dict(name='flow', required=False,
                               help="One or more flow ids to filter by. (repeatable) ex: 11851"),
                          dict(name='broadcast', required=False,
                               help="One or more broadcast ids to filter by. (repeatable) ex: 23432,34565"),
                          dict(name='before', required=False,
                               help="Only return messages before this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='after', required=False,
                               help="Only return messages after this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='relayer', required=False,
                               help="Only return messages that were received or sent by these channels. (repeatable)  ex: 515,854") ]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Send one or more messages",
                    url=reverse('api.messages'),
                    slug='sms-send',
                    request='{ "urn": ["tel:+250788222222", "tel:+250788111111"], "text": "My first message", "relayer": 1 }')

        spec['fields'] = [dict(name='urn', required=False,
                               help="A JSON array of one or more strings, each a contact URN."),
                          dict(name='contact', required=False,
                               help="A JSON array of one or more strings, each a contact UUID."),
                          dict(name='text', required=True,
                               help="The text of the message you want to send (max length 480 chars)"),
                          dict(name='relayer', required=False,
                               help="The id of the channel that should send this message, if not specified we will "
                                    "choose what it thinks is the best channel to deliver this message.")]
        return spec


class MessagesBulkActionEndpoint(generics.GenericAPIView):
    """
    ## Bulk Message Updating

    A **POST** can be used to perform an action on a set of messages in bulk.

    * **messages** - either a single message id or a JSON array of message ids (int or array of ints)
    * **action** - the action to perform, a string one of:

            label - Apply the given label to the messages
            unlabel - Remove the given label from the messages
            archive - Archive the messages
            unarchive - Un-archive the messages
            delete - Permanently delete the messages

    * **label** - the name of a label (string, optional)
    * **label_uuid** - the UUID of a label (string, optional)

    Example:

        POST /api/v1/message_actions.json
        {
            "messages": [1234, 2345, 3456],
            "action": "label",
            "label": "Testing"
        }

    You will receive an empty response if successful.
    """
    permission = 'msgs.msg_api'
    permission_classes = (SSLPermission, ApiPermission)
    serializer_class = MsgBulkActionSerializer

    def post(self, request, *args, **kwargs):
        user = request.user
        serializer = self.serializer_class(user=user, data=request.DATA)

        if serializer.is_valid():
            return Response('', status=status.HTTP_204_NO_CONTENT)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Update one or more messages",
                    url=reverse('api.message_actions'),
                    slug='msg-actions',
                    request='{ "messages": [12345, 23456], "action": "label", '
                            '"label_uuid": "fdd156ca-233a-48c1-896d-a9d594d59b95" }')

        spec['fields'] = [dict(name='messages', required=True,
                               help="A JSON array of one or more integers, each a message id."),
                          dict(name='action', required=True,
                               help="One of the following strings: label, unlabel, archive, unarchive, delete"),
                          dict(name='label', required=False,
                               help="The name of a label if the action is label or unlabel"),
                          dict(name='label_uuid', required=False,
                               help="The UUID of a label if the action is label or unlabel")]
        return spec


class LabelsEndpoint(generics.ListAPIView):
    """
    ## Listing Message Labels

    A **GET** returns the list of message labels for your organization, in the order of last created.

    * **uuid** - the UUID of the label (string) (filterable: ```uuid``` repeatable)
    * **name** - the name of the label (string) (filterable: ```name```)
    * **parent** - the UUID of the parent label (string) (filterable: ```parent```)
    * **count** - the number of messages with the label (int)

    Example:

        GET /api/v1/labels.json

    Response containing the groups for your organization:

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
                {
                    "uuid": "fdd156ca-233a-48c1-896d-a9d594d59b95",
                    "name": "Screened",
                    "parent": null,
                    "count": 315
                },
                ...
            ]
        }

    ## Adding a Label

    A **POST** can be used to create a new message label. Don't specify a UUID as this will be generated for you.

    * **name** - the label name (string)
    * **parent** - the UUID of an existing label which will be the parent (string, optional)

    Example:

        POST /api/v1/labels.json
        {
            "name": "Screened",
            "parent": null
        }

    You will receive a label object (with the new UUID) as a response if successful:

        {
            "uuid": "fdd156ca-233a-48c1-896d-a9d594d59b95",
            "name": "Screened",
            "parent": null,
            "count": 0
        }

    ## Updating a Label

    A **POST** can also be used to update an message label if you do specify it's UUID.

    * **uuid** - the label UUID
    * **name** - the label name (string)
    * **parent** - the UUID of an existing label which will be the parent (string, optional)

    Example:

        POST /api/v1/labels.json
        {
            "uuid": "fdd156ca-233a-48c1-896d-a9d594d59b95",
            "name": "Checked",
            "parent": null
        }

    You will receive the updated label object as a response if successful:

        {
            "uuid": "fdd156ca-233a-48c1-896d-a9d594d59b95",
            "name": "Checked",
            "parent": null,
            "count": 0
        }
    """
    permission = 'msgs.label_api'
    model = Label
    serializer_class = LabelReadSerializer
    permission_classes = (SSLPermission, ApiPermission)
    form_serializer_class = LabelWriteSerializer

    def post(self, request, format=None):
        user = request.user
        serializer = LabelWriteSerializer(user=user, data=request.DATA)
        if serializer.is_valid():
            serializer.save()
            response_serializer = LabelReadSerializer(instance=serializer.object)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = Label.objects.filter(org=self.request.user.get_org()).order_by('-pk')

        name = self.request.QUERY_PARAMS.get('name', None)
        if name:
            queryset = queryset.filter(name__icontains=name)

        uuids = self.request.QUERY_PARAMS.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        parents = self.request.QUERY_PARAMS.getlist('parent', None)
        if parents:
            queryset = queryset.filter(parent__uuid__in=parents)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Message Labels",
                    url=reverse('api.labels'),
                    slug='label-list',
                    request="")
        spec['fields'] = [dict(name='name', required=False,
                               help="The name of the message label to return.  ex: Priority"),
                          dict(name='uuid', required=False,
                               help="The UUID of the message label to return. (repeatable) ex: fdd156ca-233a-48c1-896d-a9d594d59b95")]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add or Update a Message Label",
                    url=reverse('api.labels'),
                    slug='label-update',
                    request='{ "name": "Screened", "parent": null }')

        spec['fields'] = [dict(name='uuid', required=True,
                               help='The UUID of the message label.  ex: "fdd156ca-233a-48c1-896d-a9d594d59b95"'),
                          dict(name='name', required=False,
                               help='The name of the message label.  ex: "Screened"'),
                          dict(name='parent', required=False,
                               help='The UUID of the parent label. ex: "34914a7c-911d-4768-8adb-ac75fb6e9b94"')]
        return spec


class Calls(generics.ListAPIView):
    """
    Returns the incoming and outgoing calls for your organization, most recent first.

      * **relayer** - which channel received or placed this call (int) (filterable: ```channel```)
      * **phone** - the phone number of the caller or callee depending on the call_type (string) (filterable: ```phone``` repeatable)
      * **created_on** - when the call was received or sent (datetime) (filterable: ```before``` and ```after```)
      * **duration** - the duration of the call in seconds (int, 0 for missed calls)
      * **call_type** - one of the following strings: (filterable: ```call_type``` repeatable)

             mt_call - Outgoing Call
             mo_call - Incoming Call
             mo_miss - Missed Incoming Call

    Example:

        GET /api/v1/calls.json

    Response:

        {
            "count": 4,
            "next": null,
            "previous": null,
            "results": [
            {
                "call": 4,
                "relayer": 2,
                "phone": "+250788123123",
                "created_on": "2013-02-27T09:06:15",
                "duration": 606,
                "call_type": "mt_call"
            },
            ...

    """
    permission = 'msgs.call_api'
    model = Call
    serializer_class = CallSerializer
    permission_classes = (SSLPermission, ApiPermission)

    def get_queryset(self):
        queryset = Call.objects.filter(org=self.request.user.get_org(), is_active=True).order_by('-created_on')

        ids = splitting_getlist(self.request, 'call')
        if ids:
            queryset = queryset.filter(pk__in=ids)

        before = self.request.QUERY_PARAMS.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except:
                queryset = queryset.filter(pk=-1)

        after = self.request.QUERY_PARAMS.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except:
                queryset = queryset.filter(pk=-1)

        call_types = splitting_getlist(self.request, 'call_type')
        if call_types:
            queryset = queryset.filter(call_type__in=call_types)

        phones = splitting_getlist(self.request, 'phone')
        if phones:
            queryset = queryset.filter(contact__urns__path__in=phones)

        channel = self.request.QUERY_PARAMS.get('relayer', None)
        if channel:
            try:
                channel = int(channel)
                queryset = queryset.filter(channel_id=channel)
            except:
                queryset = queryset.filter(pk=-1)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List recent incoming and outgoing Calls",
                    url=reverse('api.calls'),
                    slug='call-list',
                    request="after=2013-01-01T00:00:00.000&phone=%2B250788123123")
        spec['fields'] = [ dict(name='call', required=False,
                                help="One or more call ids to filter by.  ex: 2335,2320"),
                           dict(name='call_type', required=False,
                                help="One or more types of calls to filter by. (repeatable)  ex: mo_miss"),
                           dict(name='phone', required=False,
                                help="One or more phone numbers to filter by in E164 format. (repeatable) ex: +250788123123"),
                           dict(name='before', required=False,
                                help="Only return messages before this date.  ex: 2012-01-28T18:00:00.000"),
                           dict(name='after', required=False,
                                help="Only return messages after this date.  ex: 2012-01-28T18:00:00.000"),
                           dict(name='relayer', required=False,
                                help="Only return messages that were received or sent by these channels.  ex: 515,854") ]

        return spec


class Channels(DestroyModelMixin, generics.ListAPIView):
    """
    ## Claiming Channels

    You can associate a new Android channel to your account by sending a **POST** request to this URL with the following
    JSON data:

    * **claim_code** - the unique claim code displayed on the Android application after registration (string)
    * **phone** - the phone number for the channel (string)
    * **name** - the desired name for this channel (string, optional)

    Example:

        POST /api/v1/relayers.json
        {
            "claim_code": "APFOJFQW9",
            "phone": "+250788123123",
            "name": "MTN Rwanda"
        }

    You will receive a channel object as a response if successful:

        {
            "relayer": 5,
            "phone": "+250788123123",
            "name": "MTN Rwanda",
            "country": "RW",
            "last_seen": "2013-03-01T05:31:27",
            "power_level": 99,
            "power_status": "STATUS_DISCHARGING",
            "power_source": "BATTERY",
            "network_type": "WIFI",
            "pending_message_count": 0
        }

    ## Listing Channels

    A **GET** returns the list of Android channels for your organization, in the order of last activity date.  Note that all
    status information for the device is as of the last time it was seen and can be null before the first sync.

    * **relayer** - the id of the channel (filterable: ```long``` repeatable)
    * **phone** - the phone number for the channel (string) (filterable: ```phone``` repeatable)
    * **name** - the name of this channel (string)
    * **country** - which country the sim card for this channel is registered for (string, two letter country code) (filterable: ```country``` repeatable)
    * **last_seen** - the datetime when this channel was last seen (datetime) (filterable: ```before``` and ```after```)
    * **power_level** - the power level of the device (int)
    * **power_status** - the power status, either ```STATUS_DISCHARGING``` or ```STATUS_CHARGING``` (string)
    * **power_source** - the source of power as reported by Android (string)
    * **network_type** - the type of network the device is connected to as reported by Android (string)
    * **pending_message_count** - how many messages are assigned to this channel but not yet sent (int)

    Example:

        GET /api/v1/relayers.json

    Response containing the channels for your organization:

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
            {
                "relayer": 5,
                "phone": "+250788123123",
                "name": "Android Phone",
                "country": "RW",
                "last_seen": "2013-03-01T05:31:27",
                "power_level": 99,
                "power_status": "STATUS_DISCHARGING",
                "power_source": "BATTERY",
                "network_type": "WIFI",
                "pending_message_count": 0
            }]
        }

    ## Removing Channels

    A **DELETE** removes all matching channels from your account.  You can filter the list of channels to remove
    using the same attributes as the list call above.

    * **relayer** - the id of the the channel (long) (filterable: ```long``` repeatable)
    * **phone** - the phone number of the channel to remove (string) (filterable: ```string``` repeatable)
    * **before** - only delete channels which have not been seen since this date (string) ex: 2012-01-28T18:00:00.000
    * **after** - only delete channels which have been seen since this date (string) ex: 2012-01-28T18:00:00.000
    * **country** - the country this channel is in (string, two letter country code)(filterable: ```country``` repeatable)

    Example:

        DELETE /api/v1/relayers.json?id=409

    You will receive either a 404 response if no matching channels were found, or a 204 response if one or more channels
    was removed.

    """
    permission = 'channels.channel_api'
    model = Channel
    serializer_class = ChannelReadSerializer
    permission_classes = (SSLPermission, ApiPermission)
    form_serializer_class = ChannelClaimSerializer

    def post(self, request, format=None):
        user = request.user
        serializer = ChannelClaimSerializer(user=user, data=request.DATA)
        if serializer.is_valid():
            serializer.save()

            response_serializer = ChannelReadSerializer(instance=serializer.object)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self.request = request
        queryset = self.get_queryset()

        if not queryset:
            return Response(status=status.HTTP_404_NOT_FOUND)
        else:
            for channel in queryset:
                channel.release()
            return Response(status=status.HTTP_204_NO_CONTENT)

    def get_queryset(self):
        queryset = Channel.objects.filter(org=self.request.user.get_org(), is_active=True).order_by('-last_seen')

        ids = splitting_getlist(self.request, 'relayer')
        if ids:
            queryset = queryset.filter(pk__in=ids)

        phones = splitting_getlist(self.request, 'phone')
        if phones:
            queryset = queryset.filter(address__in=phones)

        before = self.request.QUERY_PARAMS.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(last_seen__lte=before)
            except:
                queryset = queryset.filter(pk=-1)

        after = self.request.QUERY_PARAMS.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(last_seen__gte=after)
            except:
                queryset = queryset.filter(pk=-1)

        countries = splitting_getlist(self.request, 'country')
        if countries:
            queryset = queryset.filter(country__in=countries)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Android phones",
                    url=reverse('api.channels'),
                    slug='channel-list',
                    request="after=2013-01-01T00:00:00.000&country=RW")
        spec['fields'] = [ dict(name='relayer', required=False,
                                help="One or more channel ids to filter by. (repeatable)  ex: 235,124"),
                           dict(name='phone', required=False,
                                help="One or more phone number to filter by. (repeatable)  ex: +250788123123,+250788456456"),
                           dict(name='before', required=False,
                                help="Only return channels which were last seen before this date.  ex: 2012-01-28T18:00:00.000"),
                           dict(name='after', required=False,
                                help="Only return channels which were last seen after this date.  ex: 2012-01-28T18:00:00.000"),
                           dict(name='country', required=False,
                                help="Only channels which are active in countries with these country codes. (repeatable) ex: RW") ]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Attach a Channel to your account using a claim code",
                    url=reverse('api.channels'),
                    slug='channel-claim',
                    request='{ "claim_code": "AOIFUGQUF", "phone": "+250788123123", "name": "Rwanda MTN Channel" }')

        spec['fields'] = [ dict(name='claim_code', required=True,
                                help="The 9 character claim code displayed by the Android application after startup.  ex: FJUQOGIEF"),
                           dict(name='phone', required=True,
                                help="The phone number of the channel.  ex: +250788123123"),
                           dict(name='name', required=False,
                                help="A friendly name you want to assign to this channel.  ex: MTN Rwanda") ]
        return spec

    @classmethod
    def get_delete_explorer(cls):
        spec = dict(method="DELETE",
                    title="Delete Android phones",
                    url=reverse('api.channels'),
                    slug='channel-delete',
                    request="after=2013-01-01T00:00:00.000&country=RW")
        spec['fields'] = [ dict(name='relayer', required=False,
                                help="Only delete channels with these ids. (repeatable)  ex: 235,124"),
                           dict(name='phone', required=False,
                                help="Only delete channels with these phones numbers. (repeatable)  ex: +250788123123,+250788456456"),
                           dict(name='before', required=False,
                                help="Only delete channels which were last seen before this date.  ex: 2012-01-28T18:00:00.000"),
                           dict(name='after', required=False,
                                help="Only delete channels which were last seen after this date.  ex: 2012-01-28T18:00:00.000"),
                           dict(name='country', required=False,
                                help="Only delete channels which are active in countries with these country codes. (repeatable) ex: RW") ]

        return spec


class Groups(generics.ListAPIView):
    """
    ## Listing Groups

    A **GET** returns the list of groups for your organization, in the order of last created.

    * **uuid** - the UUID of the group (string) (filterable: ```uuid``` repeatable)
    * **name** - the name of the group (string) (filterable: ```name```)
    * **size** - the number of contacts in the group (int)

    Example:

        GET /api/v1/groups.json

    Response containing the groups for your organization:

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
                {
                    "uuid": "5f05311e-8f81-4a67-a5b5-1501b6d6496a",
                    "name": "Reporters",
                    "size": 315
                },
                ...
            ]
        }
    """
    permission = 'contacts.contactgroup_api'
    model = ContactGroup
    serializer_class = ContactGroupReadSerializer
    permission_classes = (SSLPermission, ApiPermission)

    def get_queryset(self):
        queryset = ContactGroup.user_groups.filter(org=self.request.user.get_org(), is_active=True).order_by('created_on')

        name = self.request.QUERY_PARAMS.get('name', None)
        if name:
            queryset = queryset.filter(name__icontains=name)

        uuids = self.request.QUERY_PARAMS.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Contact Groups",
                    url=reverse('api.contactgroups'),
                    slug='contactgroup-list',
                    request="")
        spec['fields'] = [dict(name='name', required=False,
                               help="The name of the Contact Group to return.  ex: Reporters"),
                          dict(name='uuid', required=False,
                               help="The UUID of the Contact Group to return. (repeatable) ex: 5f05311e-8f81-4a67-a5b5-1501b6d6496a")]

        return spec


class Contacts(generics.ListAPIView):
    """
    ## Adding a Contact

    You can add a new contact to your account, or update the fields on a contact by sending a **POST** request to this
    URL with the following JSON data:

    * **uuid** - the UUID of the contact to update (string) (optional, new contact created if not present)
    * **name** - the full name of the contact (string, optional)
    * **language** - the preferred language for the contact (3 letter iso code, optional)
    * **urns** - a list of URNs you want associated with the contact (string array)
    * **group_uuids** - a list of the UUIDs of any groups this contact is part of (string array, optional)
    * **fields** - a JSON dictionary of contact fields you want to set or update on this contact (JSON, optional)

    Example:

        POST /api/v1/contacts.json
        {
            "name": "Ben Haggerty",
            "language": "eng",
            "urns": ["tel:+250788123123", "twitter:ben"],
            "group_uuids": ["6685e933-26e1-4363-a468-8f7268ab63a9", "1281f10a-d5b3-4580-a0fe-92adb97c2d1a"],
            "fields": {
              "nickname": "Macklemore",
              "side_kick": "Ryan Lewis"
            }
        }

    You will receive a contact object as a response if successful:

        {
            "uuid": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
            "name": "Ben Haggerty",
            "language": "eng",
            "urns": ["tel:+250788123123", "twitter:ben"],
            "group_uuids": ["6685e933-26e1-4363-a468-8f7268ab63a9", "1281f10a-d5b3-4580-a0fe-92adb97c2d1a"],
            "fields": {
              "nickname": "Macklemore",
              "side_kick": "Ryan Lewis"
            }
        }

    ## Updating Contacts

    You can update contacts in the same manner as adding them but we recommend you pass in the UUID for the contact
    as a way of specifying which contact to update. Note that when you pass in the contact UUID and ```urns```, all
    existing URNs will be evaluated against this new set and updated accordingly.

    ## Listing Contacts

    A **GET** returns the list of contacts for your organization, in the order of last activity date.

    * **uuid** - the unique identifier for this contact (string) (filterable: ```uuid``` repeatable)
    * **name** - the name of this contact (string, optional)
    * **language** - the preferred language of this contact (string, optional)
    * **urns** - the URNs associated with this contact (string array) (filterable: ```urns```)
    * **group_uuids** - the UUIDs of any groups this contact is part of (string array, optional) (filterable: ```group_uuids``` repeatable)
    * **fields** - any contact fields on this contact (JSON, optional)

    Example:

        GET /api/v1/contacts.json

    Response containing the contacts for your organization:

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
            {
                "uuid": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
                "name": "Ben Haggerty",
                "language": null,
                "urns": [
                  "tel:+250788123123"
                ],
                "group_uuids": ["6685e933-26e1-4363-a468-8f7268ab63a9", "1281f10a-d5b3-4580-a0fe-92adb97c2d1a"],
                "fields": {
                  "nickname": "Macklemore",
                  "side_kick": "Ryan Lewis"
                }
            }]
        }

    ## Removing Contacts

    A **DELETE** removes all matching contacts from your account. You must provide either a list of UUIDs or a list of
    URNs, or both.

    * **uuid** - the unique identifiers for these contacts (string array)
    * **urns** - the URNs associated with these contacts (string array)

    Example:

        DELETE /api/v1/contacts.json?uuid=27fb583b-3087-4778-a2b3-8af489bf4a93

    You will receive either a 404 response if no matching contacts were found, or a 204 response if one or more contacts
    were removed.
    """
    permission = 'contacts.contact_api'
    model = Contact
    serializer_class = ContactReadSerializer
    permission_classes = (SSLPermission, ApiPermission)
    form_serializer_class = ContactWriteSerializer

    def post(self, request, format=None):
        user = request.user
        serializer = ContactWriteSerializer(user=user, data=request.DATA)
        if serializer.is_valid():
            serializer.save()
            response_serializer = ContactReadSerializer(instance=serializer.object)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        queryset = self.get_base_queryset(request)

        # to make it harder for users to delete all their contacts by mistake, we require them to filter by UUID or urns
        uuids = request.QUERY_PARAMS.getlist('uuid', None)
        urns = request.QUERY_PARAMS.getlist('urns', None)

        if not (uuids or urns):
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if uuids:
            queryset = queryset.filter(uuid__in=uuids)
        if urns:
            queryset = queryset.filter(urns__urn__in=urns)

        if not queryset:
            return Response(status=status.HTTP_404_NOT_FOUND)
        else:
            for contact in queryset:
                contact.release()
            return Response(status=status.HTTP_204_NO_CONTENT)

    def get_base_queryset(self, request):
        return Contact.objects.filter(org=request.user.get_org(), is_active=True, is_test=False)

    def get_queryset(self):
        queryset = self.get_base_queryset(self.request).order_by('modified_on')

        phones = splitting_getlist(self.request, 'phone')  # deprecated, use urns
        if phones:
            queryset = queryset.filter(urns__path__in=phones, urns__scheme=TEL_SCHEME)

        urns = self.request.QUERY_PARAMS.getlist('urns', None)
        if urns:
            queryset = queryset.filter(urns__urn__in=urns)

        groups = self.request.QUERY_PARAMS.getlist('group', None)  # deprecated, use group_uuids
        if groups:
            queryset = queryset.filter(all_groups__name__in=groups,
                                       all_groups__group_type=USER_DEFINED_GROUP)

        group_uuids = self.request.QUERY_PARAMS.getlist('group_uuids', None)
        if group_uuids:
            queryset = queryset.filter(all_groups__uuid__in=group_uuids,
                                       all_groups__group_type=USER_DEFINED_GROUP)

        uuids = self.request.QUERY_PARAMS.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        return queryset

    def paginate_queryset(self, queryset, page_size):
        """
        Overriding this method let's us jump in just after queryset has been paginated but before objects have been
        serialized - so that we can perform some cache optimization
        """
        packed = MultipleObjectMixin.paginate_queryset(self, queryset, page_size)
        paginator, page, object_list, is_paginated = packed

        # initialize caches of all contact fields and URNs
        org = self.request.user.get_org()

        # convert to list before cache initialization so that these will be the contact objects which get serialized
        page.object_list = list(page.object_list)
        Contact.bulk_cache_initialize(org, page.object_list)

        return packed

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Contacts",
                    url=reverse('api.contacts'),
                    slug='contact-list',
                    request="phone=%2B250788123123")
        spec['fields'] = [dict(name='uuid', required=False,
                               help="One or more UUIDs to filter by. (repeatable) ex: 27fb583b-3087-4778-a2b3-8af489bf4a93"),
                          dict(name='urns', required=False,
                               help="One or more URNs to filter by.  ex: tel:+250788123123,twitter:ben"),
                          dict(name='group_uuids', required=False,
                               help="One or more group UUIDs to filter by. (repeatable) ex: 6685e933-26e1-4363-a468-8f7268ab63a9")]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add or update a Contact",
                    url=reverse('api.contacts'),
                    slug='contact-update',
                    request='{ "name": "Ben Haggerty", "groups": ["Top 10 Artists"], "urns": ["tel:+250788123123"] }')

        spec['fields'] = [dict(name='name', required=False,
                               help="The name of the contact.  ex: Ben Haggerty"),
                          dict(name='language', required=False,
                               help="The 3 letter iso code for the preferred language of the contact.  ex: fre, eng"),
                          dict(name='urns', required=True,
                               help='The URNs of the contact.  ex: ["tel:+250788123123"]'),
                          dict(name='group_uuids', required=False,
                               help='The UUIDs of groups this contact should be part of, as a string array.  ex: ["6685e933-26e1-4363-a468-8f7268ab63a9"]'),
                          dict(name='fields', required=False,
                               help='Any fields to set on the contact, as a JSON dictionary. ex: { "Delivery Date": "2012-10-10 5:00" }')]
        return spec

    @classmethod
    def get_delete_explorer(cls):
        spec = dict(method="DELETE",
                    title="Delete Contacts",
                    url=reverse('api.contacts'),
                    slug='contact-delete',
                    request="uuid=27fb583b-3087-4778-a2b3-8af489bf4a93")
        spec['fields'] = [dict(name='uuid', required=False,
                               help="One or more UUIDs to filter by. (repeatable) ex: 27fb583b-3087-4778-a2b3-8af489bf4a93"),
                          dict(name='urns', required=False,
                               help="One or more URNs to filter by.  ex: tel:+250788123123,twitter:ben"),
                          dict(name='group_uuids', required=False,
                               help="One or more group UUIDs to filter by. (repeatable) ex: 6685e933-26e1-4363-a468-8f7268ab63a9")]
        return spec


class FieldsEndpoint(generics.ListAPIView):
    """
    ## Listing Fields

    A **GET** returns the list of fields for your organization.

    * **key** - the unique key of this field (string) (filterable: ```key```)
    * **label** - the display label of this field (string)
    * **value_type** - one of the following strings:

             T - Text
             N - Decimal Number
             D - Datetime
             S - State
             I - District

    Example:

        GET /api/v1/fields.json

    Response containing the groups for your organization:

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
                {
                    "key": "nick_name",
                    "label": "Nick name",
                    "value_type": "T"
                },
                ...
            ]
        }

    ## Adding a Field

    A **POST** can be used to create a new contact field. Don't specify a key as this will be generated for you.

    * **label** - the display label (string)
    * **value_type** - one of the value type codes (string)

    Example:

        POST /api/v1/fields.json
        {
            "label": "Nick name",
            "value_type": "T"
        }

    You will receive a field object (with the new field key) as a response if successful:

        {
            "key": "nick_name",
            "label": "Nick name",
            "value_type": "T"
        }

    ## Updating a Field

    A **POST** can also be used to update an existing field if you do specify it's key.

    * **key** - the unique field key
    * **label** - the display label (string)
    * **value_type** - one of the value type codes (string)

    Example:

        POST /api/v1/fields.json
        {
            "key": "nick_name",
            "label": "New label",
            "value_type": "T"
        }

    You will receive the updated field object as a response if successful:

        {
            "key": "nick_name",
            "label": "New label",
            "value_type": "T"
        }
    """
    permission = 'contacts.contactfield_api'
    model = ContactField
    serializer_class = ContactFieldReadSerializer
    permission_classes = (SSLPermission, ApiPermission)
    form_serializer_class = ContactFieldWriteSerializer

    def post(self, request, format=None):
        user = request.user
        serializer = ContactFieldWriteSerializer(user=user, data=request.DATA)
        if serializer.is_valid():
            serializer.save()
            response_serializer = ContactFieldReadSerializer(instance=serializer.object)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = ContactField.objects.filter(org=self.request.user.get_org(), is_active=True)

        key = self.request.QUERY_PARAMS.get('key', None)
        if key:
            queryset = queryset.filter(key__icontains=key)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Contact Fields",
                    url=reverse('api.contactfields'),
                    slug='contactfield-list',
                    request="")
        spec['fields'] = [dict(name='key', required=False,
                               help="The key of the Contact Field to return.  ex: state")]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add or update a Contact Field",
                    url=reverse('api.contactfields'),
                    slug='contactfield-update',
                    request='{ "key": "nick_name", "label": "Nick name", "value_type": "T" }')

        spec['fields'] = [dict(name='key',
                               help='The unique key of the field, required when updating a field, generated for new fields.  ex: "nick_name"'),
                          dict(name='label', required=False,
                               help='The label of the field.  ex: "Nick name"'),
                          dict(name='value_type', required=False,
                               help='The value type code. ex: T, N, D, S, I')]
        return spec


class FlowResultsEndpoint(generics.GenericAPIView):
    """
    This endpoint allows you to get aggregate results for a flow ruleset, optionally segmenting the results by another
    ruleset in the process.

    ## Retrieving Flow Results

    By making a ```GET``` request you can retrieve a dictionary representing the results for the rulesets in a flow.

    Example:

       GET /api/v1/results.json

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
                {
                    "flow": 1056,
                    "id": 4237,
                    "label": "Gender",
                    "node": "5acfa6d5-be4a-4bcc-8011-d1bd9dfasffa",
                    "results": [
                        {
                            "categories": [
                                {
                                    "count": 501,
                                    "label": "Male"
                                },
                                {
                                    "count": 409,
                                    "label": "Female"
                                }
                            ],
                            "label": "All"
                        }
                    ]
                }
           ...
    """
    permission = 'flows.flow_results'
    model = Flow
    permission_classes = (SSLPermission, ApiPermission)
    serializer_class = ResultSerializer

    def get(self, request, format=None):
        user = request.user
        org = user.get_org()

        ruleset, contact_field = None, None

        id = self.request.QUERY_PARAMS.get('ruleset', None)
        if id:
            try:
                int(id)
                ruleset = RuleSet.objects.filter(flow__org=org, pk=id).first()
            except ValueError:
                ruleset = RuleSet.objects.filter(flow__org=org, uuid=id).first()

            if not ruleset:
                return Response(dict(contact_field="No ruleset found with that id"), status=status.HTTP_400_BAD_REQUEST)

        field = self.request.QUERY_PARAMS.get('contact_field', None)
        if field:
            contact_field = ContactField.objects.filter(org=org, label__iexact=field).first()
            if not contact_field:
                return Response(dict(contact_field="No contact field found with that label"), status=status.HTTP_400_BAD_REQUEST)

        if (not ruleset and not contact_field) or (ruleset and contact_field):
            return Response(dict(ruleset="You must specify either a ruleset or contact field",
                                 contact_field="You must specify either a ruleset or contact field"), status=status.HTTP_400_BAD_REQUEST)

        segment = self.request.QUERY_PARAMS.get('segment', None)
        if segment:
            try:
                segment = json.loads(segment)
            except:
                return Response(dict(segment="Invalid segment format, must be in JSON format"))

        serializer = ResultSerializer(ruleset=ruleset, contact_field=contact_field, segment=segment)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="Get summarized results for a RuleSet or Contact Field",
                    url=reverse('api.results'),
                    slug='flow-results',
                    request="")
        spec['fields'] = [dict(name='flow', required=False,
                               help="One or more flow ids to filter by.  ex: 234235,230420"),
                          dict(name='ruleset', required=False,
                               help="One or more rulesets to filter by.  ex: 12412,12451"),
                         ]
        return spec


class FlowRunEndpoint(generics.ListAPIView):
    """
    This endpoint allows you to list and start flow runs.  A run represents a single contact's path through a flow. A
    run is created for each time a contact is started down a flow.

    ## Listing Flow Runs

    By making a ```GET``` request you can list all the flow runs for your organization, filtering them as needed.  Each
    run has the following attributes:

    * **run** - the id of the run (long) (filterable: ```run``` repeatable)
    * **flow_uuid** - the UUID of the flow (string) (filterable: ```flow_uuid``` repeatable)
    * **contact** - the UUID of the contact this run applies to (string) filterable: ```contact``` repeatable)
    * **group_uuids** - the UUIDs of any groups this contact is part of (string array, optional) (filterable: ```group_uuids``` repeatable)
    * **created_on** - the datetime when this run was started (datetime) (filterable: ```before``` and ```after```)
    * **steps** - steps visited by the contact on the flow (array of dictionaries)
    * **values** - values collected during the flow run (array of dictionaries)

    Example:

        GET /api/v1/runs.json?flow_uuid=f5901b62-ba76-4003-9c62-72fdacc1b7b7

    Response is the list of runs on the flow, most recent first:

        {
            "count": 389,
            "next": "/api/v1/runs/?page=2",
            "previous": null,
            "results": [
            {
                "uuid": "988e040c-33ff-4917-a36e-8cfa6a5ac731",
                "flow_uuid": "f5901b62-ba76-4003-9c62-72fdacc1b7b7",
                "contact": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
                "created_on": "2013-03-02T17:28:12",
                "steps": [
                    {
                        "node": "22bd934e-953b-460d-aaf5-42a84ec8f8af",
                        "left_on": "2013-08-19T19:11:21.082Z",
                        "text": "Hi from the Thrift Shop!  We are having specials this week. What are you interested in?",
                        "value": null,
                        "arrived_on": "2013-08-19T19:11:21.044Z",
                        "type": "A"
                    },
                    {
                        "node": "9a31495d-1c4c-41d5-9018-06f93baa5b98",
                        "left_on": null,
                        "text": "I want to buy a fox skin",
                        "value": "fox skin",
                        "arrived_on": "2013-08-19T19:11:21.088Z",
                        "type": "R"
                    }
                ],
                "values": [
                    {
                        "step_uuid": "9a31495d-1c4c-41d5-9018-06f93baa5b98",
                        "category": "fox skins",
                        "text": "I want to buy a fox skin",
                        "label": "Interest",
                        "value": "fox skin",
                        "time": "2013-05-30T08:53:58.531Z"
                    }
                ],
            },
            ...
        }

    ## Start a Flow Run for a Contact

    By making a ```POST``` request to the endpoint you can add a contact to a flow with a specified set of 'extra' variables.

    * **flow_uuid** - the UUID of the flow to start (string)
    * **contacts** - the UUIDs of the contacts to start in the flow (string array, optional)
    * **groups** - the UUIDs of any groups this contact is part of (string array, optional)
    * **extra** - a set of extra variables. (dictionary)
    * **restart_participants** - a boolean if True force restart of contacts already in a flow. (boolean, optional, defaults to True)

    Example:

        POST /api/v1/runs.json
        {
            "flow_uuid": "f5901b62-ba76-4003-9c62-72fdacc1b7b7",
            "groups": ["b775ea51-b847-4a20-b668-6c4ce2f61356"]
            "contacts": ["09d23a05-47fe-11e4-bfe9-b8f6b119e9ab", "f23777a3-e606-41d8-a925-3d87370e1a2b"],
            "restart_participants": true,
            "extra": {
                "confirmation_code":"JFI0358F98",
                "name":"Ryan Lewis"
            }
        }

    Response is the runs that have been started for the contacts:

        [
            {
                "run": 1234,
                "flow_uuid": "f5901b62-ba76-4003-9c62-72fdacc1b7b7",
                "contact": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
                "created_on": "2013-08-19T19:11:20.838Z"
                "values": [],
                "steps": [
                    {
                        "node": "22bd934e-953b-460d-aaf5-42a84ec8f8af",
                        "left_on": "2013-08-19T19:11:21.082Z",
                        "text": "Hi Ryan Lewis from the Thrift Shop!  We are having specials this week. What are you interested in?",
                        "value": null,
                        "arrived_on": "2013-08-19T19:11:21.044Z",
                        "type": "A"
                    },
                    {
                        "node": "9a31495d-1c4c-41d5-9018-06f93baa5b98",
                        "left_on": null,
                        "text": null,
                        "value": null,
                        "arrived_on": "2013-08-19T19:11:21.088Z",
                        "type": "R"
                    }
                ]
            },
            ...
        ]

    """
    permission = 'flows.flow_api'
    model = FlowRun
    permission_classes = (SSLPermission, ApiPermission)
    serializer_class = FlowRunReadSerializer


    def post(self, request, format=None):
        user = request.user
        serializer = FlowRunStartSerializer(user=user, data=request.DATA)
        if serializer.is_valid():
            serializer.save()

            if serializer.object:
                response_serializer = FlowRunReadSerializer(instance=serializer.object)
                return Response(response_serializer.data, status=status.HTTP_201_CREATED)
            return Response(dict(non_field_errors=["All contacts are already started in this flow, "
                                                   "use restart_participants to force them to restart in the flow"]),
                                 status=status.HTTP_400_BAD_REQUEST)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = FlowRun.objects.filter(flow__org=self.request.user.get_org(), contact__is_test=False).order_by('-created_on')

        runs = splitting_getlist(self.request, 'run')
        if runs:
            queryset = queryset.filter(pk__in=runs)

        flows = splitting_getlist(self.request, 'flow')  # deprecated, use flow_uuid
        if flows:
            queryset = queryset.filter(flow__pk__in=flows)

        flow_uuids = splitting_getlist(self.request, 'flow_uuid')
        if flow_uuids:
            queryset = queryset.filter(flow__uuid__in=flow_uuids)

        before = self.request.QUERY_PARAMS.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except:
                queryset = queryset.filter(pk=-1)

        after = self.request.QUERY_PARAMS.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except:
                queryset = queryset.filter(pk=-1)

        phones = splitting_getlist(self.request, 'phone')  # deprecated
        if phones:
            queryset = queryset.filter(contact__urns__path__in=phones)

        groups = splitting_getlist(self.request, 'group')  # deprecated, use group_uuids
        if groups:
            queryset = queryset.filter(contact__all_groups__name__in=groups,
                                       contact__all_groups__group_type=USER_DEFINED_GROUP)

        group_uuids = splitting_getlist(self.request, 'group_uuids')
        if group_uuids:
            queryset = queryset.filter(contact__all_groups__uuid__in=group_uuids,
                                       contact__all_groups__group_type=USER_DEFINED_GROUP)

        contacts = splitting_getlist(self.request, 'contact')
        if contacts:
            queryset = queryset.filter(contact__uuid__in=contacts)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Flow Runs",
                    url=reverse('api.runs'),
                    slug='run-list',
                    request="after=2013-01-01T00:00:00.000")
        spec['fields'] = [dict(name='run', required=False,
                               help="One or more run ids to filter by. (repeatable) ex: 1234,1235"),
                          dict(name='flow_uuid', required=False,
                               help="One or more flow UUIDs to filter by. (repeatable) ex: f5901b62-ba76-4003-9c62-72fdacc1b7b7"),
                          dict(name='contact', required=False,
                               help="One or more contact UUIDs to filter by. (repeatable) ex: 09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"),
                          dict(name='group_uuids', required=False,
                               help="One or more group UUIDs to filter by.(repeatable)  ex: 6685e933-26e1-4363-a468-8f7268ab63a9"),
                          dict(name='before', required=False,
                               help="Only return runs which were created before this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='after', required=False,
                               help="Only return runs which were created after this date.  ex: 2012-01-28T18:00:00.000")]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add one or more contacts to a Flow",
                    url=reverse('api.runs'),
                    slug='run-post',
                    request='{ "flow": 15015, "phone": ["+250788222222", "+250788111111"], "extra": { "item_id": "ONEZ", "item_price":"$3.99" } }')

        spec['fields'] = [ dict(name='flow', required=True,
                                help="The id of the flow to start the contact(s) on, the flow cannot be archived"),
                           dict(name='phone', required=True,
                                help="A JSON array of one or more strings, each a phone number in E164 format"),
                           dict(name='contact', required=False,
                                help="A JSON array of one or more strings, each a contact UUID"),
                           dict(name='extra', required=False,
                                help="A dictionary of key/value pairs to include as the @extra parameters in the flow (max of twenty values of 255 chars or less)") ]
        return spec


class CampaignEndpoint(generics.ListAPIView):
    """
    ## Adding or Updating a Campaign

    You can add a new campaign to your account, or update the fields on a campaign by sending a **POST** request to this
    URL with the following data:

    * **campaign** - the id of the campaign (integer, optional, only include if updating an existing campaign)
    * **name** - the full name of the campaign (string, required)
    * **group** - the name of the contact group this campaign will be run against (string, required)

    Example:

        POST /api/v1/campaigns.json
        {
            "name": "Starting Over",
            "group": "Macklemore & Ryan Lewis"
        }

    You will receive a campaign object as a response if successful:

        {
            "campaign": 1251125,
            "name": "Starting Over",
            "group": "Macklemore & Ryan Lewis",
            "created_on": "2013-08-19T19:11:21.088Z"
        }

    ## Listing Campaigns

    You can retrieve the campaigns for your organization by sending a ```GET``` to the same endpoint, listing the
    most recently created campaigns first.

      * **campaign** - the id of the campaign (int) (filterable: ```campaign``` repeatable)
      * **name** - the name of this campaign (string)
      * **group** - the name of the group this campaign operates on (string)
      * **created_on** - the datetime when this campaign was created (datetime) (filterable: ```before``` and ```after```)

    Example:

        GET /api/v1/campaigns.json

    Response is a list of the campaigns on your account

        {
            "count": 15,
            "next": "/api/v1/campaigns/?page=2",
            "previous": null,
            "results": [
            {
                "campaign": 145145,
                "name": "Starting Over",
                "group": "Macklemore & Ryan Lewis",
                "created_on": "2013-08-19T19:11:21.088Z"
            },
            ...
        }

    """
    permission = 'campaigns.campaign_api'
    model = Campaign
    permission_classes = (SSLPermission, ApiPermission)
    serializer_class = CampaignSerializer
    form_serializer_class = CampaignWriteSerializer

    def post(self, request, format=None):
        user = request.user
        serializer = CampaignWriteSerializer(user=user, data=request.DATA)
        if serializer.is_valid():
            serializer.save()
            response_serializer = CampaignSerializer(instance=serializer.object)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = Campaign.objects.filter(org=self.request.user.get_org(), is_active=True, is_archived=False).order_by('-created_on')

        ids = splitting_getlist(self.request, 'campaign')
        if ids:
            queryset = queryset.filter(pk__in=ids)

        before = self.request.QUERY_PARAMS.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except:
                queryset = queryset.filter(pk=-1)

        after = self.request.QUERY_PARAMS.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except:
                queryset = queryset.filter(pk=-1)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Campaigns",
                    url=reverse('api.campaigns'),
                    slug='campaign-list',
                    request="after=2013-01-01T00:00:00.000")
        spec['fields'] = [ dict(name='campaign', required=False,
                                help="One or more campaign ids to filter by. (repeatable)  ex: 234235,230420"),
                           dict(name='before', required=False,
                                help="Only return flows which were created before this date.  ex: 2012-01-28T18:00:00.000"),
                           dict(name='after', required=False,
                                help="Only return flows which were created after this date.  ex: 2012-01-28T18:00:00.000"),
                           ]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add or update a Campaign",
                    url=reverse('api.campaigns'),
                    slug='campaign-update',
                    request='{ "name": "Starting Over", "group": "Macklemore & Ryan Lewis" }')

        spec['fields'] = [ dict(name='campaign', required=False,
                                help="The id of the campaign to update. (optional, new campaign will be created if left out)  ex: 1241515"),
                           dict(name='name', required=True,
                                help='The name of the campaign.  ex: "Starting Over"'),
                           dict(name='group', required=True,
                                help='What contact group the campaign should operate against.  ex: "Macklemore & Ryan Lewis"'),
                         ]
        return spec


class CampaignEventEndpoint(generics.ListAPIView):
    """
    ## Adding or Updating Campaign Events

    You can add a new event to your campaign, or update the fields on an event by sending a **POST** request to this
    URL with the following data:

    * **event** - the id of the event (integer, optional, only include if updating an existing campaign)
    * **campaign** - the id of the campaign this event should be part of (integer, only include when creating new events)
    * **relative_to** - the field that this event will be relative to for the contact (string, name of the Contact field, required)
    * **offset** - the offset from our contact field (positive or negative integer, required)
    * **unit** - the unit for our offset, M (minutes), H (hours), D (days), W (weeks) (string, required)
    * **delivery_hour** - the hour of the day to deliver the message (integer 0-24, -1 indicates send at the same hour as the Contact Field)
    * **message** - the message to send to the contact (string, required if flow id is not included)
    * **flow** - the id of the flow to start the contact down (integer, required if message is null)

    Example:

        POST /api/v1/events.json
        {
            "campaign": 1231515,
            "relative_to": "Last Hit",
            "offset": 160,
            "unit": "W",
            "delivery_hour": -1,
            "message": "Feeling sick and helpless, lost the compass where self is."
        }

    You will receive an event object as a response if successful:

        {
            "event": 150001,
            "campaign": 1251125,
            "relative_to": "Last Hit",
            "offset": 160,
            "unit": "W",
            "delivery_hour": -1,
            "message": "Feeling sick and helpless, lost the compass where self is."
            "created_on": "2013-08-19T19:11:21.088Z"
        }

    ## Listing Events

    You can retrieve the campaign events for your organization by sending a ```GET``` to the same endpoint, listing the
    most recently created campaigns first.

      * **campaign** - the id of the campaign (int) (filterable: ```campaign``` repeatable)
      * **event** - only return events with these ids (int) (filterable: ```event``` repeatable)
      * **created_on** - the datetime when this campaign was created (datetime) (filterable: ```before``` and ```after```)

    Example:

        GET /api/v1/events.json

    Response is a list of the campaign events on your account

        {
            "count": 15,
            "next": "/api/v1/campaigns/?page=2",
            "previous": null,
            "results": [
            {
                "event": 150001,
                "campaign": 1251125,
                "relative_to": "Last Hit",
                "offset": 180,
                "unit": "W",
                "delivery_hour": -1,
                "message": "If I can be an example of being sober, then I can be an example of starting over.",
                "created_on": "2013-08-19T19:11:21.088Z"
            },
            ...
        }

    ## Removing Events

    A **DELETE** to the endpoint removes all matching events from your account.  You can filter the list of events to remove
    using the following attributes

    * **campaing** - remove only events which are part of this campaign (comma separated int)
    * **event** - remove only events with these ids (comma separated int)

    Example:

        DELETE /api/v1/events.json?event=409,501

    You will receive either a 404 response if no matching events were found, or a 204 response if one or more events
    was removed.
    """
    permission = 'campaigns.campaignevent_api'
    model = CampaignEvent
    permission_classes = (SSLPermission, ApiPermission)
    serializer_class = CampaignEventSerializer
    form_serializer_class = CampaignEventWriteSerializer

    def post(self, request, format=None):
        user = request.user
        serializer = CampaignEventWriteSerializer(user=user, data=request.DATA)
        if serializer.is_valid():
            serializer.save()
            response_serializer = CampaignEventSerializer(instance=serializer.object)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self.request = request
        queryset = self.get_queryset()

        if not queryset:
            return Response(status=status.HTTP_404_NOT_FOUND)
        else:
            queryset.update(is_active=False)
            return Response(status=status.HTTP_204_NO_CONTENT)

    def get_queryset(self):
        queryset = CampaignEvent.objects.filter(campaign__org=self.request.user.get_org(), is_active=True).order_by('-created_on')

        ids = splitting_getlist(self.request, 'campaign')
        if ids:
            queryset = queryset.filter(campaign__pk__in=ids)

        ids = splitting_getlist(self.request, 'event')
        if ids:
            queryset = queryset.filter(pk__in=ids)

        before = self.request.QUERY_PARAMS.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except:
                queryset = queryset.filter(pk=-1)

        after = self.request.QUERY_PARAMS.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except:
                queryset = queryset.filter(pk=-1)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Campaign Events",
                    url=reverse('api.campaignevents'),
                    slug='campaignevent-list',
                    request="after=2013-01-01T00:00:00.000")
        spec['fields'] = [ dict(name='campaign', required=False,
                                help="One or more campaign ids to filter by. (repeatable) ex: 234235,230420"),
                           dict(name='event', required=False,
                                help="One or more even ids to filter by. (repeatable) ex:3435,67464"),
                           dict(name='before', required=False,
                                help="Only return flows which were created before this date.  ex: 2012-01-28T18:00:00.000"),
                           dict(name='after', required=False,
                                help="Only return flows which were created after this date.  ex: 2012-01-28T18:00:00.000"),
                           ]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add or update a Campaign Event",
                    url=reverse('api.campaignevents'),
                    slug='campaignevent-update',
                    request='{ "campaign": 1251125, "relative_to": "Last Hit", "offset": 180, "unit": "W", "delivery_hour": -1, "message": "If I can be an example of being sober, then I can be an example of starting over."}')

        spec['fields'] = [ dict(name='event', required=False,
                                help="The id of the event to update. (optional, new event will be created if left out)  ex: 1241515"),
                           dict(name='campaign', required=False,
                                help="The id of the campaign this even is part of. (optional, only used when creating a new campaign)  ex: 15151"),
                           dict(name='relative_to', required=True,
                                help='The name of the Contact field this event is relative to. (string) ex: "Last Fix"'),
                           dict(name='offset', required=True,
                                help='The offset, as an integer to the relative_to field (integer, positive or negative)  ex: 15'),
                           dict(name='unit', required=True,
                                help='The unit of the offset, one of M for minutes, H for hours, D for days or W for weeks (string)  ex: "M"'),
                           dict(name='delivery_hour', required=True,
                                help='The hour this event should be triggered, or -1 if the event should be sent at the same hour as our date (integer, -1 or 0-23)  ex: "16"'),
                           dict(name='message', required=False,
                                help='The message that should be sent to the contact when this event is triggered. (string)  ex: "It is time to raise the roof."'),
                           dict(name='flow', required=False,
                                help='If not message is included, then the id of the flow that the contact should start when this event is triggered (integer)  ex: 1514'),
                         ]
        return spec

    @classmethod
    def get_delete_explorer(cls):
        spec = dict(method="DELETE",
                    title="Delete a Campaign Event from a Campaign",
                    url=reverse('api.campaignevents'),
                    slug='campaignevent-delete',
                    request="event=1255")
        spec['fields'] = [ dict(name='event', required=False,
                                help="Only delete events with these ids ids. (repeatable) ex: 235,124"),
                           dict(name='campaign', required=False,
                                help="Only delete events that are part of these campaigns. ex: 1514,141") ]

        return spec


class BoundaryEndpoint(generics.ListAPIView):
    """
    This endpoint allows you to list the administrative boundaries for the country associated with your organization
    along with the simplified gps geometry for those boundaries in GEOJSON format.

    ## Listing Boundaries

    Returns the boundaries for your organization.

    **Note that this may be a very large dataset as it includes the simplified coordinates for each administrative boundary.
     It is recommended to cache the results on the client side.**

      * **name** - the name of the administrative boundary (string)
      * **boundary** - the internal id for this administrative boundary, this is a variation on the OSM id (string)
      * **parent** - the id of the containing parent of this administrative boundary, null if this boundary is a country (string)
      * **level** - the level of the boundary, 0 for country levels, 1 for state levels, 2 for district levels (int)
      * **geometry** - the geojson geometry for this boundary, this will usually be a MultiPolygon (geojson)

    Example:

        GET /api/v1/boundaries.json

    Response is a list of the boundaries on your account

        {
            "count": 809,
            "next": "/api/v1/boundaries/?page=2",
            "previous": null,
            "results": [
            {
                "name": "Aba North",
                "boundary": "R3713502",
                "parent": "R3713501",
                "level": 1,
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [
                            [
                                [
                                    7.5251021,
                                    5.0504713
                                ],
                                [
                                    7.5330272,
                                    5.0423498
                                ]
                            ]
                        ]
                    ]
                }
            },
            ...
        }

    """
    permission = 'locations.adminboundary_api'
    model = AdminBoundary
    permission_classes = (SSLPermission, ApiPermission)
    serializer_class = BoundarySerializer

    def get_queryset(self):
        org = self.request.user.get_org()
        if not org.country:
            return []

        queryset = AdminBoundary.objects.filter(Q(pk=org.country.pk) |
                                                Q(parent=org.country) |
                                                Q(parent__parent=org.country)).order_by('level', 'name')
        return queryset.select_related('parent')

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List the Administrative Boundaries",
                    url=reverse('api.boundaries'),
                    slug='boundary-list',
                    request="")
        spec['fields'] = []

        return spec


class FlowEndpoint(generics.ListAPIView):
    """
    This endpoint allows you to list all the active flows on your account using the ```GET``` method.

    ## Listing Flows

    Returns the flows for your organization, listing the most recent flows first.

      * **uuid** - the UUID of the flow (string) (filterable: ```uuid``` repeatable)
      * **name** - the name of the flow (string)
      * **archived** - whether this flow is archived (boolean) (filterable: ```archived```)
      * **labels** - the labels for this flow (string array) (filterable: ```label``` repeatable)
      * **created_on** - the datetime when this flow was created (datetime) (filterable: ```before``` and ```after```)
      * **rulesets** - the rulesets on this flow, including their node UUID and label

    Example:

        GET /api/v1/flows.json

    Response is a list of the flows on your account

        {
            "count": 12,
            "next": "/api/v1/flows/?page=2",
            "previous": null,
            "results": [
            {
                "uuid": "cf85cb74-a4e4-455b-9544-99e5d9125cfd",
                "archived": false,
                "name": "Thrift Shop Status",
                "labels": [ "Polls" ],
                "rulesets": [
                   {
                    "id": 17122,
                    "node": "fe594710-68fc-4cb5-bd85-c0c77e4caa45",
                    "label": "Age"
                   },
                   {
                    "id": 17128,
                    "node": "fe594710-68fc-4cb5-bd85-c0c77e4caa45",
                    "label": "Gender"
                   }
                ]
            },
            ...
        }

    """
    permission = 'flows.flow_api'
    model = Flow
    permission_classes = (SSLPermission, ApiPermission)
    serializer_class = FlowReadSerializer
    form_serializer_class = FlowWriteSerializer

    def post(self, request, format=None):
        user = request.user
        serializer = FlowWriteSerializer(user=user, data=request.DATA)
        if serializer.is_valid():
            serializer.save()
            response_serializer = FlowReadSerializer(instance=serializer.object)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        queryset = Flow.objects.filter(org=self.request.user.get_org(), is_active=True).order_by('-created_on')

        uuids = self.request.QUERY_PARAMS.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        ids = self.request.QUERY_PARAMS.getlist('flow', None)  # deprecated, use uuid
        if ids:
            queryset = queryset.filter(pk__in=ids)

        before = self.request.QUERY_PARAMS.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except:
                queryset = queryset.filter(pk=-1)

        after = self.request.QUERY_PARAMS.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except:
                queryset = queryset.filter(pk=-1)

        label = self.request.QUERY_PARAMS.getlist('label', None)
        if label:
            queryset = queryset.filter(labels__name__in=label)

        archived = self.request.QUERY_PARAMS.get('archived', None)
        if archived is not None:
            queryset = queryset.filter(is_archived=str_to_bool(archived))

        return queryset.prefetch_related('labels')

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Flows",
                    url=reverse('api.flows'),
                    slug='flow-list',
                    request="after=2013-01-01T00:00:00.000")
        spec['fields'] = [ dict(name='flow', required=False,
                                help="One or more flow ids to filter by. (repeatable) ex: 234235,230420"),
                           dict(name='before', required=False,
                                help="Only return flows which were created before this date. ex: 2012-01-28T18:00:00.000"),
                           dict(name='after', required=False,
                                help="Only return flows which were created after this date. ex: 2012-01-28T18:00:00.000"),
                           dict(name='label', required=False,
                                help="Only return flows with this label. (repeatable) ex: Polls"),
                           dict(name='archived', required=False,
                                help="Filter returned flows based on whether they are archived. ex: Y")
                           ]

        return spec


class AssetEndpoint(generics.RetrieveAPIView):
    """
    This endpoint allows you to fetch assets associated with your account using the ```GET``` method.
    """
    def retrieve(self, request, *args, **kwargs):
        type_name = request.GET.get('type')
        identifier = request.GET.get('identifier')
        if not type_name or not identifier:
            return HttpResponseBadRequest("Must provide type and identifier")

        if type_name not in AssetType.__members__:
            return HttpResponseBadRequest("Invalid asset type: %s" % type_name)

        return handle_asset_request(request.user, AssetType[type_name], identifier)


# ====================================================================================================================
# Channel handlers
# ====================================================================================================================


class TwilioHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(TwilioHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("ILLEGAL METHOD")

    def post(self, request, *args, **kwargs):
        from twilio.util import RequestValidator
        from temba.msgs.models import Msg, SENT, DELIVERED

        signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')
        url = "https://" + settings.TEMBA_HOST + "%s" % request.get_full_path()

        call_sid = request.REQUEST.get('CallSid', None)
        direction = request.REQUEST.get('Direction', None)
        status = request.REQUEST.get('CallStatus', None)
        to_number = request.REQUEST.get('To', None)
        to_country = request.REQUEST.get('ToCountry', None)
        from_number = request.REQUEST.get('From', None)

        # Twilio sometimes sends un-normalized numbers
        if not to_number.startswith('+') and to_country:
            to_number, valid = ContactURN.normalize_number(to_number, to_country)

        # see if it's a twilio call being initiated
        if to_number and call_sid and direction == 'inbound' and status == 'ringing':

            # find a channel that knows how to answer twilio calls
            channel = Channel.objects.filter(address=to_number, channel_type='T', role__contains='A', is_active=True).exclude(org=None).first()
            if not channel:
                raise Exception("No active answering channel found for number: %s" % to_number)

            client = channel.org.get_twilio_client()
            validator = RequestValidator(client.auth[1])
            signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')

            base_url = settings.TEMBA_HOST
            url = "https://%s%s" % (base_url, request.get_full_path())

            if validator.validate(url, request.POST, signature):
                from temba.ivr.models import IVRCall

                # find a contact for the one initiating us
                contact_urn = ContactURN.get_or_create(channel.org, TEL_SCHEME, from_number, channel)
                contact = Contact.get_or_create(channel.org, channel.created_by, urns=[(TEL_SCHEME, from_number)])
                flow = Trigger.find_flow_for_inbound_call(contact)

                call = IVRCall.create_incoming(channel, contact, contact_urn, flow, channel.created_by)
                call.update_status(request.POST.get('CallStatus', None),
                                   request.POST.get('CallDuration', None))
                call.save()

                if flow:
                    FlowRun.create(flow, contact, call=call)
                    response = Flow.handle_call(call, {})
                    return HttpResponse(unicode(response))
                else:

                    # we don't have an inbound trigger to deal with this call.
                    response = twiml.Response()

                    # say nothing and hangup, this is a little rude, but if we reject the call, then
                    # they'll get a non-working number error. We send 'busy' when our server is down
                    # so we don't want to use that here either.
                    response.say('')
                    response.hangup()

                    # if they have a missed call trigger, fire that off
                    Trigger.catch_triggers(contact, MISSED_CALL_TRIGGER)

                    # either way, we need to hangup now
                    return HttpResponse(unicode(response))

        action = request.GET.get('action', 'received')
        # this is a callback for a message we sent
        if action == 'callback':
            smsId = request.GET.get('id', None)
            status = request.POST.get('SmsStatus', None)

            # get the SMS
            sms = Msg.objects.select_related('channel').get(id=smsId)

            # validate this request is coming from twilio
            org = sms.org
            client = org.get_twilio_client()
            validator = RequestValidator(client.auth[1])

            if not validator.validate(url, request.POST, signature):
                # raise an exception that things weren't properly signed
                raise ValidationError("Invalid request signature")

            # queued, sending, sent, failed, or received.
            if status == 'sent':
                sms.status_sent()
            elif status == 'delivered':
                sms.status_delivered()
            elif status == 'failed':
                sms.fail()

            sms.broadcast.update()

            return HttpResponse("", status=200)

        # this is an incoming message that is being received by Twilio
        elif action == 'received':
            channel = Channel.objects.filter(address=to_number, is_active=True).exclude(org=None).first()
            if not channel:
                raise Exception("No active channel found for number: %s" % to_number)

            # validate this request is coming from twilio
            org = channel.org
            client = org.get_twilio_client()
            validator = RequestValidator(client.auth[1])

            if not validator.validate(url, request.POST, signature):
                # raise an exception that things weren't properly signed
                raise ValidationError("Invalid request signature")

            Msg.create_incoming(channel, (TEL_SCHEME, request.POST['From']), request.POST['Body'])

            return HttpResponse("", status=201)

        return HttpResponse("Not Handled, unknown action", status=400)

class StripeHandler(View): # pragma: no cover
    """
    Handles WebHook events from Stripe.  We are interested as to when invoices are
    charged by Stripe so we can send the user an invoice email.
    """
    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(StripeHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("ILLEGAL METHOD")

    def post(self, request, *args, **kwargs):
        import stripe
        from temba.orgs.models import Org, TopUp

        # stripe delivers a JSON payload
        stripe_data = json.loads(request.body)

        # but we can't trust just any response, so lets go look up this event
        stripe.api_key = get_stripe_credentials()[1]
        event = stripe.Event.retrieve(stripe_data['id'])

        if not event:
            return HttpResponse("Ignored, no event")

        if not event.livemode:
            return HttpResponse("Ignored, test event")

        # we only care about invoices being paid or failing
        if event.type == 'charge.succeeded' or event.type == 'charge.failed':
            charge = event.data.object
            charge_date = datetime.fromtimestamp(charge.created)
            description = charge.description
            amount = "$%s" % (Decimal(charge.amount) / Decimal(100)).quantize(Decimal(".01"))

            # look up our customer
            customer = stripe.Customer.retrieve(charge.customer)

            # and our org
            org = Org.objects.filter(stripe_customer=customer.id).first()
            if not org:
                return HttpResponse("Ignored, no org for customer")

            # look up the topup that matches this charge
            topup = TopUp.objects.filter(stripe_charge=charge.id).first()
            if topup and event.type == 'charge.failed':
                topup.rollback()
                topup.save()

            # we know this org, trigger an event for a payment succeeding
            if org.administrators.all():
                if event.type == 'charge_succeeded':
                    track = "temba.charge_succeeded"
                else:
                    track = "temba.charge_failed"

                context = dict(description=description,
                               invoice_id=charge.id,
                               invoice_date=charge_date.strftime("%b %e, %Y"),
                               amount=amount,
                               org=org.name,
                               cc_last4=charge.card.last4,
                               cc_type=charge.card.type,
                               cc_name=charge.card.name)

                admin_email = org.administrators.all().first().email

                analytics.track(admin_email, track, context)
                return HttpResponse("Event '%s': %s" % (track, context))

        # empty response, 200 lets Stripe know we handled it
        return HttpResponse("Ignored, uninteresting event")


class AfricasTalkingHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(AfricasTalkingHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("ILLEGAL METHOD", status=400)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import AFRICAS_TALKING

        action = kwargs['action']
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=AFRICAS_TALKING).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # this is a callback for a message we sent
        if action == 'delivery':
            if not 'status' in request.POST or not 'id' in request.POST:
                return HttpResponse("Missing status or id parameters", status=400)

            status = request.POST['status']
            external_id = request.POST['id']

            # look up the message
            sms = Msg.objects.filter(channel=channel, external_id=external_id).first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % external_id, status=404)

            if status == 'Success':
                sms.status_delivered()
            elif status == 'Sent' or status == 'Buffered':
                sms.status_sent()
            elif status == 'Rejected' or status == 'Failed':
                sms.fail()

            sms.broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'callback':
            if not 'from' in request.POST or not 'text' in request.POST:
                return HttpResponse("Missing from or text parameters", status=400)

            sms = Msg.create_incoming(channel, (TEL_SCHEME, request.POST['from']), request.POST['text'])

            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)


class ZenviaHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(ZenviaHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import ZENVIA

        request.encoding = "ISO-8859-1"

        action = kwargs['action']
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=ZENVIA).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # this is a callback for a message we sent
        if action == 'status':
            if not 'status' in request.REQUEST or not 'id' in request.REQUEST:
                return HttpResponse("Missing parameters, requires 'status' and 'id'", status=400)

            status = int(request.REQUEST['status'])
            sms_id = request.REQUEST['id']

            # look up the message
            sms = Msg.objects.filter(channel=channel, pk=sms_id).first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % sms_id, status=404)

            # delivered
            if status == 120:
                sms.status_delivered()
            elif status == 111:
                sms.status_sent()
            else:
                sms.fail()

            # update our broadcast status
            sms.broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            import pytz

            if not 'date' in request.REQUEST or not 'from' in request.REQUEST or not 'msg' in request.REQUEST:
                return HttpResponse("Missing parameters, requires 'from', 'date' and 'msg'", status=400)

            # dates come in the format 31/07/2013 14:45:00
            sms_date = datetime.strptime(request.REQUEST['date'], "%d/%m/%Y %H:%M:%S")
            brazil_date = pytz.timezone('America/Sao_Paulo').localize(sms_date)

            sms = Msg.create_incoming(channel,
                                      (TEL_SCHEME, request.REQUEST['from']),
                                      request.REQUEST['msg'],
                                      date=brazil_date)

            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)


class ExternalHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(ExternalHandler, self).dispatch(*args, **kwargs)

    def get_channel_type(self):
        from temba.channels.models import EXTERNAL
        return EXTERNAL

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED

        action = kwargs['action'].lower()
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=self.get_channel_type()).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=400)

        # this is a callback for a message we sent
        if action == 'delivered' or action == 'failed' or action == 'sent':
            if not 'id' in request.REQUEST:
                return HttpResponse("Missing 'id' parameter, invalid call.", status=400)

            sms_pk = request.REQUEST['id']

            # look up the message
            sms = Msg.objects.filter(channel=channel, pk=sms_pk).first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % sms_pk, status=400)

            if action == 'delivered':
                sms.status_delivered()
            elif action == 'sent':
                sms.status_sent()
            elif action == 'failed':
                sms.fail()

            sms.broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'received':
            if not request.REQUEST.get('from', None):
                return HttpResponse("Missing 'from' parameter, invalid call.", status=400)

            if not 'text' in request.REQUEST:
                return HttpResponse("Missing 'text' parameter, invalid call.", status=400)

            sms = Msg.create_incoming(channel, (TEL_SCHEME, request.REQUEST['from']), request.REQUEST['text'])

            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)


class ShaqodoonHandler(ExternalHandler):
    """
    Overloaded external channel for accepting Shaqodoon messages
    """
    def get_channel_type(self):
        from temba.channels.models import SHAQODOON
        return SHAQODOON


class InfobipHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(InfobipHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import INFOBIP

        action = kwargs['action'].lower()
        channel_uuid = kwargs['uuid']

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=INFOBIP).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # parse our raw body, it should be XML that looks something like:
        # <DeliveryReport>
        #   <message id="254021015120766124"
        #    sentdate="2014/02/10 16:12:07"
        #    donedate="2014/02/10 16:13:00"
        #    status="DELIVERED"
        #    gsmerror="0"
        #    price="0.65" />
        # </DeliveryReport>
        root = ET.fromstring(request.body)

        message = root.find('message')
        external_id = message.get('id')
        status = message.get('status')

        # look up the message
        sms = Msg.objects.filter(channel=channel, external_id=external_id).first()
        if not sms:
            return HttpResponse("No SMS message with external id: %s" % external_id, status=404)

        if status == 'DELIVERED':
            sms.status_delivered()
        elif status == 'SENT':
            sms.status_sent()
        elif status in ['NOT_SENT', 'NOT_ALLOWED', 'INVALID_DESTINATION_ADDRESS',
                        'INVALID_SOURCE_ADDRESS', 'ROUTE_NOT_AVAILABLE', 'NOT_ENOUGH_CREDITS',
                        'REJECTED', 'INVALID_MESSAGE_FORMAT']:
            sms.fail()

        sms.broadcast.update()

        return HttpResponse("SMS Status Updated")

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import INFOBIP

        action = kwargs['action'].lower()
        channel_uuid = kwargs['uuid']

        # validate all the appropriate fields are there
        if 'sender' not in request.REQUEST or 'text' not in request.REQUEST or 'receiver' not in request.REQUEST:
            return HttpResponse("Missing parameters, must have 'sender', 'text' and 'receiver'", status=400)

        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=INFOBIP).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # validate this is not a delivery report, those must be POSTs
        if action == 'delivered':
            return HttpResponse("Illegal method, delivery reports must be POSTs", status=401)

        # make sure the channel number matches the receiver
        if channel.address != '+' + request.REQUEST['receiver']:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        sms = Msg.create_incoming(channel, (TEL_SCHEME, request.REQUEST['sender']), request.REQUEST['text'])

        return HttpResponse("SMS Accepted: %d" % sms.id)


class Hub9Handler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(Hub9Handler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import HUB9

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=HUB9).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=404)

        # They send everythign as a simple GET
        # userid=testusr&password=test&original=555555555555&sendto=666666666666
        # &messageid=99123635&message=Test+sending+sms

        action = kwargs['action'].lower()
        message = request.REQUEST.get('message', None)
        external_id = request.REQUEST.get('messageid', None)
        status = int(request.REQUEST.get('status', -1))
        from_number = request.REQUEST.get('original', None)
        to_number = request.REQUEST.get('sendto', None)

        # delivery reports
        if action == 'delivered':
            # look up the message
            sms = Msg.objects.filter(channel=channel, pk=external_id).first()
            if not sms:
                return HttpResponse("No SMS message with external id: %s" % external_id, status=404)

            if 10 <= status <= 12:
                sms.status_delivered()
            elif status > 20:
                sms.fail()
            elif status != -1:
                sms.status_sent()

            sms.broadcast.update()
            return HttpResponse("000")

        # An MO message
        if action == 'received':
            # make sure the channel number matches the receiver
            if channel.address != '+' + to_number:
                return HttpResponse("Channel with number '%s' not found." % to_number, status=404)

            from_number = '+' + from_number
            Msg.create_incoming(channel, (TEL_SCHEME, from_number), message)
            return HttpResponse("000")

        return HttpResponse("Unreconized action: %s" % action, status=404)


class HighConnectionHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(HighConnectionHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg
        from temba.channels.models import HIGH_CONNECTION

        channel_uuid = kwargs['uuid']
        channel = Channel.objects.filter(uuid=channel_uuid, is_active=True, channel_type=HIGH_CONNECTION).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel with uuid: %s not found." % channel_uuid, status=400)

        action = kwargs['action'].lower()

        # Update on the status of a sent message
        if action == 'status':
            msg_id = request.REQUEST.get('ret_id', None)
            status = int(request.REQUEST.get('status', 0))

            # look up the message
            sms = Msg.objects.filter(channel=channel, pk=msg_id).first()
            if not sms:
                return HttpResponse("No SMS message with id: %s" % msg_id, status=400)

            if status == 4:
                sms.status_sent()
            elif status == 6:
                sms.status_delivered()
            elif status in [2, 11, 12, 13, 14, 15, 16]:
                sms.fail()

            sms.broadcast.update()
            return HttpResponse(json.dumps(dict(msg="Status Updated")))

        # An MO message
        elif action == 'receive':
            to_number = request.REQUEST.get('TO', None)
            from_number = request.REQUEST.get('FROM', None)
            message = request.REQUEST.get('MESSAGE', None)
            received = request.REQUEST.get('RECEPTION_DATE', None)

            # dateformat for reception date is 2015-04-02T14:26:06 in UTC
            if received is None:
                received = timezone.now()
            else:
                raw_date = datetime.strptime(received, "%Y-%m-%dT%H:%M:%S")
                received = raw_date.replace(tzinfo=pytz.utc)

            if to_number is None or from_number is None or message is None:
                return HttpResponse("Missing TO, FROM or MESSAGE parameters", status=400)

            msg = Msg.create_incoming(channel, (TEL_SCHEME, from_number), message, date=received)
            return HttpResponse(json.dumps(dict(msg="Msg received", id=msg.id)))

        return HttpResponse("Unrecognized action: %s" % action, status=400)

class NexmoHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(NexmoHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, FAILED, DELIVERED
        from temba.channels.models import NEXMO

        action = kwargs['action'].lower()

        # nexmo fires a test request at our URL with no arguments, return 200 so they take our URL as valid
        if (action == 'receive' and not request.REQUEST.get('to', None)) or (action == 'status' and not request.REQUEST.get('messageId', None)):
            return HttpResponse("No to parameter, ignoring")

        request_uuid = kwargs['uuid']

        # crazy enough, for nexmo 'to' is the channel number for both delivery reports and new messages
        channel_number = request.REQUEST['to']

        # look up the channel
        channel = Channel.objects.filter(uuid=channel_number, is_active=True, channel_type=NEXMO).exclude(org=None).first()

        # make sure we got one, and that it matches the key for our org
        org_uuid = None
        if channel:
            org_uuid = channel.org.config_json().get(NEXMO_UUID, None)

        if not channel or org_uuid != request_uuid:
            return HttpResponse("Channel not found for number: %s" % channel_number, status=404)

        # this is a callback for a message we sent
        if action == 'status':
            external_id = request.REQUEST['messageId']

            # look up the message
            sms = Msg.objects.filter(channel=channel, external_id=external_id).first()
            if not sms:
                return HttpResponse("No SMS message with external id: %s" % external_id, status=200)

            status = request.REQUEST['status']

            if status == 'delivered':
                sms.status_delivered()
            elif status == 'accepted' or status == 'buffered':
                sms.status_sent()
            elif status == 'expired' or status == 'failed':
                sms.fail()

            sms.broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            number = '+%s' % request.REQUEST['msisdn']
            sms = Msg.create_incoming(channel, (TEL_SCHEME, number), request.REQUEST['text'])
            sms.external_id = request.REQUEST['messageId']
            sms.save(update_fields=['external_id'])
            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)

class VerboiceHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(VerboiceHandler, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return HttpResponse("Illegal method, must be GET", status=405)

    def get(self, request, *args, **kwargs):

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        from temba.channels.models import VERBOICE
        channel = Channel.objects.filter(uuid__iexact=request_uuid, is_active=True, channel_type=VERBOICE).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=404)

        if action == 'status':

            to = self.request.REQUEST.get('From', None)
            call_sid = self.request.REQUEST.get('CallSid', None)
            call_status = self.request.REQUEST.get('CallStatus', None)

            if not to or not call_sid or not call_status:
                return HttpResponse("Missing From or CallSid or CallStatus, ignoring message", status=400)

            from temba.ivr.models import IVRCall
            call = IVRCall.objects.filter(external_id=call_sid).first()
            if call:
                call.update_status(call_status, None)
                call.save()
                return HttpResponse("Call Status Updated")

        return HttpResponse("Not handled", status=400)

class VumiHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(VumiHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("Illegal method, must be POST", status=405)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, PENDING, QUEUED, WIRED, SENT, DELIVERED, FAILED, ERRORED
        from temba.channels.models import VUMI

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type=VUMI).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=404)

        # parse our JSON
        try:
            body = json.loads(request.body)
        except Exception as e:
            return HttpResponse("Invalid JSON: %s" % unicode(e), status=400)

        # this is a callback for a message we sent
        if action == 'event':
            if not 'event_type' in body and not 'user_message_id' in body:
                return HttpResponse("Missing event_type or user_message_id, ignoring message", status=400)

            external_id = body['user_message_id']
            status = body['event_type']

            # look up the message
            sms = Msg.objects.filter(channel=channel, external_id=external_id)

            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % external_id, status=404)

            if not status in ['ack', 'delivery_report']:
                return HttpResponse("Unknown status '%s', ignoring", status=200)

            # only update to SENT status if still in WIRED state
            if status == 'ack':
                sms.filter(status__in=[PENDING, QUEUED, WIRED]).update(status=SENT)
            elif status == 'delivery_report':
                sms = sms.first()
                if sms:
                    delivery_status = body.get('delivery_status', 'success')
                    if delivery_status == 'failed':

                        # we can get multiple reports from vumi if they multi-part the message for us
                        if sms.status in (WIRED, DELIVERED):
                            print "!! [%d] marking %s message as error" % (sms.pk, sms.get_status_display())
                            Msg.mark_error(get_redis_connection(), sms)
                    else:

                        # we should only mark it as delivered if it's in a wired state, we want to hold on to our
                        # delivery failures if any part of the message comes back as failed
                        if sms.status == WIRED:
                            sms.status_delivered()

            # disabled for performance reasons
            # sms.first().broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            if not 'timestamp' in body or not 'from_addr' in body or not 'content' in body or not 'message_id' in body:
                return HttpResponse("Missing one of timestamp, from_addr, content or message_id, ignoring message", status=400)

            # dates come in the format "2014-04-18 03:54:20.570618" GMT
            sms_date = datetime.strptime(body['timestamp'], "%Y-%m-%d %H:%M:%S.%f")
            gmt_date = pytz.timezone('GMT').localize(sms_date)

            sms = Msg.create_incoming(channel,
                                      (TEL_SCHEME, body['from_addr']),
                                      body['content'],
                                      date=gmt_date)

            # use an update so there is no race with our handling
            Msg.objects.filter(pk=sms.id).update(external_id=body['message_id'])
            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)

class KannelHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(KannelHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, DELIVERED, FAILED, WIRED, PENDING, QUEUED
        from temba.channels.models import KANNEL

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type=KANNEL).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=400)

        # kannel is telling us this message got delivered
        if action == 'status':
            if not all(k in request.REQUEST for k in ['id', 'status']):
                return HttpResponse("Missing one of 'id' or 'status' in request parameters.", status=400)

            sms_id = self.request.REQUEST['id']

            # look up the message
            sms = Msg.objects.filter(channel=channel, id=sms_id)
            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % sms_id, status=400)

            # possible status codes kannel will send us
            STATUS_CHOICES = {'1': DELIVERED,
                              '2': FAILED,
                              '4': SENT,
                              '8': SENT,
                              '16': FAILED}

            # check our status
            status_code = self.request.REQUEST['status']
            status = STATUS_CHOICES.get(status_code, None)

            # we don't recognize this status code
            if not status:
                return HttpResponse("Unrecognized status code: '%s', ignoring message." % status_code, status=401)

            # only update to SENT status if still in WIRED state
            if status == SENT:
                for sms_obj in sms.filter(status__in=[PENDING, QUEUED, WIRED]):
                    sms_obj.status_sent()
            elif status == DELIVERED:
                for sms_obj in sms:
                    sms_obj.status_delivered()
            elif status == FAILED:
                for sms_obj in sms:
                    sms_obj.fail()

            # disabled for performance reasons
            # sms.first().broadcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            if not all(k in request.REQUEST for k in ['message', 'sender', 'ts', 'id']):
                return HttpResponse("Missing one of 'message', 'sender', 'id' or 'ts' in request parameters.", status=400)

            # dates come in the format "2014-04-18 03:54:20.570618" GMT
            sms_date = datetime.utcfromtimestamp(int(request.REQUEST['ts']))
            gmt_date = pytz.timezone('GMT').localize(sms_date)

            sms = Msg.create_incoming(channel,
                                      (TEL_SCHEME, request.REQUEST['sender']),
                                      request.REQUEST['message'],
                                      date=gmt_date)

            Msg.objects.filter(pk=sms.id).update(external_id=request.REQUEST['id'])
            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)


class ClickatellHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(ClickatellHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, DELIVERED, FAILED, WIRED, PENDING, QUEUED
        from temba.channels.models import CLICKATELL, API_ID

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        # look up the channel
        channel = Channel.objects.filter(uuid=request_uuid, is_active=True, channel_type=CLICKATELL).exclude(org=None).first()
        if not channel:
            return HttpResponse("Channel not found for id: %s" % request_uuid, status=400)

        # make sure the API id matches if it is included (pings from clickatell don't include them)
        if 'api_id' in self.request.REQUEST and channel.config_json()[API_ID] != self.request.REQUEST['api_id']:
            return HttpResponse("Invalid API id for message delivery: %s" % self.request.REQUEST['api_id'], status=400)

        # Clickatell is telling us a message status changed
        if action == 'status':
            if not all(k in request.REQUEST for k in ['apiMsgId', 'status']):
                # return 200 as clickatell pings our endpoint during configuration
                return HttpResponse("Missing one of 'apiMsgId' or 'status' in request parameters.", status=200)

            sms_id = self.request.REQUEST['apiMsgId']

            # look up the message
            sms = Msg.objects.filter(channel=channel, external_id=sms_id)
            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % sms_id, status=400)

            # possible status codes Clickatell will send us
            STATUS_CHOICES = {'001': FAILED,      # incorrect msg id
                              '002': WIRED,       # queued
                              '003': SENT,        # delivered to upstream gateway
                              '004': DELIVERED,   # received by handset
                              '005': FAILED,      # error in message
                              '006': FAILED,      # terminated by user
                              '007': FAILED,      # error delivering
                              '008': WIRED,       # msg received
                              '009': FAILED,      # error routing
                              '010': FAILED,      # expired
                              '011': WIRED,       # delayed but queued
                              '012': FAILED,      # out of credit
                              '014': FAILED}      # too long

            # check our status
            status_code = self.request.REQUEST['status']
            status = STATUS_CHOICES.get(status_code, None)

            # we don't recognize this status code
            if not status:
                return HttpResponse("Unrecognized status code: '%s', ignoring message." % status_code, status=401)

            # only update to SENT status if still in WIRED state
            if status == SENT:
                for sms_obj in sms.filter(status__in=[PENDING, QUEUED, WIRED]):
                    sms_obj.status_sent()
            elif status == DELIVERED:
                for sms_obj in sms:
                    sms_obj.status_delivered()
            elif status == FAILED:
                for sms_obj in sms:
                    sms_obj.fail()
                    Channel.track_status(sms_obj.channel, "Failed")
            else:
                # ignore wired, we are wired by default
                pass

            # update the broadcast status
            bcast = sms.first().broadcast
            if bcast:
                bcast.update()

            return HttpResponse("SMS Status Updated")

        # this is a new incoming message
        elif action == 'receive':
            if not all(k in request.REQUEST for k in ['from', 'text', 'moMsgId', 'timestamp']):
                # return 200 as clickatell pings our endpoint during configuration
                return HttpResponse("Missing one of 'from', 'text', 'moMsgId' or 'timestamp' in request parameters.", status=200)

            # dates come in the format "2014-04-18 03:54:20" GMT
            sms_date = datetime.strptime(request.REQUEST['timestamp'], '%Y-%m-%d %H:%M:%S')
            gmt_date = pytz.timezone('GMT').localize(sms_date)
            text = request.REQUEST['text']

            # clickatell will sometimes send us UTF-16BE encoded data which is double encoded, we need to turn
            # this into utf-8 through the insane process below, Python is retarded about encodings
            if request.REQUEST.get('charset', 'utf-8') == 'UTF-16BE':
                text_bytes = bytearray()
                for text_byte in text:
                    text_bytes.append(ord(text_byte))

                # now encode back into utf-8
                text = text_bytes.decode('utf-16be').encode('utf-8')

            sms = Msg.create_incoming(channel,
                                      (TEL_SCHEME, request.REQUEST['from']),
                                      text,
                                      date=gmt_date)

            Msg.objects.filter(pk=sms.id).update(external_id=request.REQUEST['moMsgId'])
            return HttpResponse("SMS Accepted: %d" % sms.id)

        else:
            return HttpResponse("Not handled", status=400)


class PlivoHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(PlivoHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        from temba.msgs.models import Msg, SENT, DELIVERED, FAILED, WIRED, PENDING, QUEUED

        action = kwargs['action'].lower()
        request_uuid = kwargs['uuid']

        if not all(k in request.REQUEST for k in ['From', 'To', 'MessageUUID']):
                return HttpResponse("Missing one of 'From', 'To', or 'MessageUUID' in request parameters.",
                                    status=400)

        channel = Channel.objects.filter(is_active=True, uuid=request_uuid, channel_type=PLIVO).first()

        if action == 'status':
            plivo_channel_address = request.REQUEST['From']

            if not 'Status' in request.REQUEST:
                return HttpResponse("Missing 'Status' in request parameters.", status=400)

            if not channel:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            channel_address = plivo_channel_address
            if channel_address[0] != '+':
                channel_address = '+' + channel_address

            if channel.address != channel_address:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            sms_id = request.REQUEST['MessageUUID']

            if 'ParentMessageUUID' in request.REQUEST:
                sms_id = request.REQUEST['ParentMessageUUID']

            # look up the message
            sms = Msg.objects.filter(channel=channel, external_id=sms_id)
            if not sms:
                return HttpResponse("Message with external id of '%s' not found" % sms_id, status=400)

            STATUS_CHOICES = {'queued': WIRED,
                              'sent': SENT,
                              'delivered': DELIVERED,
                              'undelivered': SENT,
                              'rejected': FAILED}

            plivo_status = request.REQUEST['Status']
            status = STATUS_CHOICES.get(plivo_status, None)

            if not status:
                return HttpResponse("Unrecognized status: '%s', ignoring message." % plivo_status, status=401)

            # only update to SENT status if still in WIRED state
            if status == SENT:
                for sms_obj in sms.filter(status__in=[PENDING, QUEUED, WIRED]):
                    sms_obj.status_sent()
            elif status == DELIVERED:
                for sms_obj in sms:
                    sms_obj.status_delivered()
            elif status == FAILED:
                for sms_obj in sms:
                    sms_obj.fail()
                    Channel.track_status(sms_obj.channel, "Failed")
            else:
                # ignore wired, we are wired by default
                pass

            # update the broadcast status
            bcast = sms.first().broadcast
            if bcast:
                bcast.update()

            return HttpResponse("Status Updated")

        elif action == 'receive':
            if not 'Text' in request.REQUEST:
                return HttpResponse("Missing 'Text' in request parameters.", status=400)

            plivo_channel_address = request.REQUEST['To']

            if not channel:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            channel_address = plivo_channel_address
            if channel_address[0] != '+':
                channel_address = '+' + channel_address

            if channel.address != channel_address:
                return HttpResponse("Channel not found for number: %s" % plivo_channel_address, status=400)

            sms = Msg.create_incoming(channel,
                                      (TEL_SCHEME, request.REQUEST['From']),
                                      request.REQUEST['Text'])

            Msg.objects.filter(pk=sms.id).update(external_id=request.REQUEST['MessageUUID'])

            return HttpResponse("SMS accepted: %d" % sms.id)
        else:
            return HttpResponse("Not handled", status=400)


class MageHandler(View):

    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(MageHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return JsonResponse(dict(error="Illegal method, must be POST"), status=405)

    def post(self, request, *args, **kwargs):
        from temba.triggers.tasks import fire_follow_triggers

        authorization = request.META.get('HTTP_AUTHORIZATION', '').split(' ')

        if len(authorization) != 2 or authorization[0] != 'Token' or authorization[1] != settings.MAGE_AUTH_TOKEN:
            return JsonResponse(dict(error="Incorrect authentication token"), status=401)

        action = kwargs['action'].lower()
        new_contact = request.POST.get('new_contact', '').lower() in ('true', '1')

        if action == 'handle_message':
            try:
                msg_id = int(request.POST.get('message_id', ''))
            except ValueError:
                return JsonResponse(dict(error="Invalid message_id"), status=400)

            msg = Msg.objects.select_related('org').get(pk=msg_id)

            push_task(msg.org, HANDLER_QUEUE, HANDLE_EVENT_TASK,
                      dict(type=MSG_EVENT, id=msg.id, from_mage=True, new_contact=new_contact))

            # fire an event off for this message
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, msg, msg.created_on)
        elif action == 'follow_notification':
            try:
                channel_id = int(request.POST.get('channel_id', ''))
                contact_urn_id = int(request.POST.get('contact_urn_id', ''))
            except ValueError:
                return JsonResponse(dict(error="Invalid channel or contact URN id"), status=400)

            fire_follow_triggers.apply_async(args=(channel_id, contact_urn_id, new_contact), queue='handler')

        return JsonResponse(dict(error=None))
