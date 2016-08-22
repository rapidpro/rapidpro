from __future__ import absolute_import, unicode_literals

import json
import urllib

from django import forms
from django.contrib.auth import authenticate, login
from django.core.cache import cache
from django.db.models import Prefetch
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework import generics, mixins, status, pagination
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from smartmin.views import SmartTemplateView, SmartFormView
from temba.api.models import APIToken
from temba.assets.models import AssetType
from temba.assets.views import handle_asset_request
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import Contact, ContactField, ContactGroup, TEL_SCHEME
from temba.flows.models import Flow, FlowRun, FlowStep, RuleSet
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.msgs.models import Broadcast, Msg, Label
from temba.utils import json_date_to_datetime, splitting_getlist, str_to_bool, non_atomic_gets
from temba.values.models import Value
from ..models import APIPermission, SSLPermission
from .serializers import BoundarySerializer, AliasSerializer, BroadcastCreateSerializer, BroadcastReadSerializer
from .serializers import ChannelEventSerializer, CampaignReadSerializer, CampaignWriteSerializer
from .serializers import CampaignEventReadSerializer, CampaignEventWriteSerializer
from .serializers import ContactGroupReadSerializer, ContactReadSerializer, ContactWriteSerializer
from .serializers import ContactFieldReadSerializer, ContactFieldWriteSerializer, ContactBulkActionSerializer
from .serializers import FlowReadSerializer, FlowWriteSerializer
from .serializers import FlowRunReadSerializer, FlowRunWriteSerializer, FlowRunStartSerializer
from .serializers import MsgCreateSerializer, MsgCreateResultSerializer, MsgReadSerializer, MsgBulkActionSerializer
from .serializers import LabelReadSerializer, LabelWriteSerializer
from .serializers import ChannelClaimSerializer, ChannelReadSerializer


# caching of counts from API requests
REQUEST_COUNT_CACHE_KEY = 'org:%d:cache:api_request_counts:%s'
REQUEST_COUNT_CACHE_TTL = 5 * 60  # 5 minutes


class ApiExplorerView(SmartTemplateView):
    template_name = "api/v1/api_explorer.html"

    def get_context_data(self, **kwargs):
        context = super(ApiExplorerView, self).get_context_data(**kwargs)

        endpoints = list()
        endpoints.append(ChannelEndpoint.get_read_explorer())
        endpoints.append(ChannelEndpoint.get_write_explorer())
        endpoints.append(ChannelEndpoint.get_delete_explorer())

        endpoints.append(ContactEndpoint.get_read_explorer())
        endpoints.append(ContactEndpoint.get_write_explorer())
        endpoints.append(ContactEndpoint.get_delete_explorer())

        endpoints.append(ContactBulkActionEndpoint.get_write_explorer())

        endpoints.append(GroupEndpoint.get_read_explorer())

        endpoints.append(FieldEndpoint.get_read_explorer())
        endpoints.append(FieldEndpoint.get_write_explorer())

        endpoints.append(MessageEndpoint.get_read_explorer())
        # endpoints.append(MessageEndpoint.get_write_explorer())

        endpoints.append(MessageBulkActionEndpoint.get_write_explorer())

        endpoints.append(BroadcastEndpoint.get_read_explorer())
        endpoints.append(BroadcastEndpoint.get_write_explorer())

        endpoints.append(LabelEndpoint.get_read_explorer())
        endpoints.append(LabelEndpoint.get_write_explorer())

        endpoints.append(CallEndpoint.get_read_explorer())

        endpoints.append(FlowEndpoint.get_read_explorer())

        endpoints.append(FlowRunEndpoint.get_read_explorer())
        endpoints.append(FlowRunEndpoint.get_write_explorer())

        # endpoints.append(FlowResultsEndpoint.get_read_explorer())

        endpoints.append(CampaignEndpoint.get_read_explorer())
        endpoints.append(CampaignEndpoint.get_write_explorer())

        endpoints.append(CampaignEventEndpoint.get_read_explorer())
        endpoints.append(CampaignEventEndpoint.get_write_explorer())
        endpoints.append(CampaignEventEndpoint.get_delete_explorer())

        endpoints.append(BoundaryEndpoint.get_read_explorer())

        context['endpoints'] = endpoints

        return context


class AuthenticateEndpoint(SmartFormView):

    class LoginForm(forms.Form):
        email = forms.CharField()
        password = forms.CharField()
        role = forms.CharField()

    form_class = LoginForm

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(AuthenticateEndpoint, self).dispatch(*args, **kwargs)

    def form_valid(self, form, *args, **kwargs):
        username = form.cleaned_data.get('email')
        password = form.cleaned_data.get('password')
        role_code = form.cleaned_data.get('role')

        user = authenticate(username=username, password=password)
        if user and user.is_active:
            login(self.request, user)

            role = APIToken.get_role_from_code(role_code)
            orgs = []

            if role:
                valid_orgs = APIToken.get_orgs_for_role(user, role)

                for org in valid_orgs:
                    token = APIToken.get_or_create(org, user, role)
                    orgs.append(dict(id=org.pk, name=org.name, token=token.key))
            else:
                return HttpResponse(status=403)

            return JsonResponse(orgs, safe=False)
        else:
            return HttpResponse(status=403)


@api_view(['GET'])
@permission_classes((SSLPermission, IsAuthenticated))
def api(request, format=None):
    """
    We provide a simple REST API for you to interact with your data from outside applications.

    All endpoints should be accessed using HTTPS. The following endpoints are provided:

     * [/api/v1/boundaries](/api/v1/boundaries) - To retrieve the geometries of the administrative boundaries on your account.
     * [/api/v1/broadcasts](/api/v1/broadcasts) - To list and create outbox broadcasts.
     * [/api/v1/calls](/api/v1/calls) - To list incoming, outgoing and missed calls as reported by the Android phone.
     * [/api/v1/campaigns](/api/v1/campaigns) - To list or modify campaigns on your account.
     * [/api/v1/contacts](/api/v1/contacts) - To list or modify contacts.
     * [/api/v1/contact_actions](/api/v1/contact_actions) - To list or modify contacts.
     * [/api/v1/events](/api/v1/events) - To list or modify campaign events on your account.
     * [/api/v1/fields](/api/v1/fields) - To list or modify contact fields.
     * [/api/v1/flows](/api/v1/flows) - To list active flows
     * [/api/v1/groups](/api/v1/groups) - To list or modify campaign events on your account.
     * [/api/v1/labels](/api/v1/labels) - To list and create new message labels.
     * [/api/v1/messages](/api/v1/messages) - To list messages.
     * [/api/v1/message_actions](/api/v1/message_actions) - To perform bulk message actions.
     * [/api/v1/relayers](/api/v1/relayers) - To list, create and remove new Android phones.
     * [/api/v1/runs](/api/v1/runs) - To list or start flow runs for contacts

    You may wish to use the [API Explorer](/api/v1/explorer) to interactively experiment with the API.

    ## Web Hook

    Your application can be notified when new messages are received, sent or delivered.  You can
    configure a URL for those events to be delivered to.  Visit the [Web Hook Documentation](/webhooks/webhook/) and
    [Simulator](/webhooks/webhook/simulator/) for more details.

    ## Verbs

    All API calls follow standard REST conventions.  You can list a set of resources by making a **GET** request on the endpoint
    and either create or update resources using the **POST** verb.  You can receive responses either
    in JSON or XML by appending the corresponding extension to the endpoint URL, ex: ```/api/v1/contacts.json```

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

    **Note that all calls made through this web interface are against the live API so please exercise the appropriate caution.**
    """
    return Response({
        'boundaries': reverse('api.v1.boundaries', request=request),
        'broadcasts': reverse('api.v1.broadcasts', request=request),
        'calls': reverse('api.v1.calls', request=request),
        'campaigns': reverse('api.v1.campaigns', request=request),
        'contacts': reverse('api.v1.contacts', request=request),
        'contact_actions': reverse('api.v1.contact_actions', request=request),
        'events': reverse('api.v1.campaignevents', request=request),
        'fields': reverse('api.v1.contactfields', request=request),
        'flows': reverse('api.v1.flows', request=request),
        'labels': reverse('api.v1.labels', request=request),
        'messages': reverse('api.v1.messages', request=request),
        'message_actions': reverse('api.v1.message_actions', request=request),
        'relayers': reverse('api.v1.channels', request=request),
        'runs': reverse('api.v1.runs', request=request),
    })


class BaseAPIView(generics.GenericAPIView):
    """
    Base class of all our API endpoints
    """
    permission_classes = (SSLPermission, APIPermission)

    @non_atomic_gets
    def dispatch(self, request, *args, **kwargs):
        return super(BaseAPIView, self).dispatch(request, *args, **kwargs)


class ListAPIMixin(mixins.ListModelMixin):
    """
    Mixin for any endpoint which returns a list of objects from a GET request
    """
    pagination_class = pagination.PageNumberPagination
    cache_counts = False

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        if not kwargs.get('format', None):
            # if this is just a request to browse the endpoint docs, don't make a query
            return Response([])
        else:
            return super(ListAPIMixin, self).list(request, *args, **kwargs)

    def paginate_queryset(self, queryset):
        if self.cache_counts:
            # total counts can be expensive so we let some views cache counts based on the query parameters
            query_params = self.request.query_params.copy()
            if 'page' in query_params:
                del query_params['page']

            # param values should be in UTF8
            encoded_params = [(p[0], [v.encode('utf-8') for v in p[1]]) for p in query_params.lists()]

            query_key = urllib.urlencode(sorted(encoded_params), doseq=True)
            count_key = REQUEST_COUNT_CACHE_KEY % (self.request.user.get_org().pk, query_key)

            # only try to use cached count for pages other than the first
            if int(self.request.query_params.get('page', 1)) != 1:
                cached_count = cache.get(count_key)
                if cached_count is not None:
                    queryset.count = lambda: int(cached_count)  # monkey patch the queryset count() method

            object_list = self.paginator.paginate_queryset(queryset, self.request, view=self)

            # actual count (cached or calculated) is stored on the Django paginator rather than the REST paginator
            actual_count = int(self.paginator.page.paginator.count)

            # reset the cached value
            cache.set(count_key, actual_count, REQUEST_COUNT_CACHE_TTL)
        else:
            object_list = self.paginator.paginate_queryset(queryset, self.request, view=self)

        # give views a chance to prepare objects for serialization
        self.prepare_for_serialization(object_list)

        return object_list

    def prepare_for_serialization(self, object_list):
        """
        Views can override this to do things like bulk cache initialization of result objects
        """
        pass


class CreateAPIMixin(object):
    """
    Mixin for any endpoint which can create or update objects with a write serializer. Our list and create approach
    differs slightly a bit from ListCreateAPIView in the REST framework as we use separate read and write serializers...
    and sometimes we use another serializer again for write output
    """
    write_serializer_class = None

    def post(self, request, *args, **kwargs):
        user = request.user
        context = self.get_serializer_context()
        serializer = self.write_serializer_class(user=user, data=request.data, context=context)

        if serializer.is_valid():
            output = serializer.save()
            return self.render_write_response(output, context)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def render_write_response(self, write_output, context):
        response_serializer = self.serializer_class(instance=write_output, context=context)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class DeleteAPIMixin(object):
    """
    Mixin for any endpoint that can delete objects with a DELETE request
    """
    def delete(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)


class BroadcastEndpoint(ListAPIMixin, CreateAPIMixin, BaseAPIView):
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
    serializer_class = BroadcastReadSerializer
    write_serializer_class = BroadcastCreateSerializer
    cache_counts = True

    def post(self, request, *args, **kwargs):
        user = request.user
        if user.get_org().is_suspended():
            return Response("Sorry, your account is currently suspended. To enable sending messages, please contact support.", status=status.HTTP_400_BAD_REQUEST)
        return super(BroadcastEndpoint, self).post(request, *args, **kwargs)

    def get_queryset(self):
        queryset = self.model.objects.filter(org=self.request.user.get_org(), is_active=True).order_by('-created_on')

        ids = splitting_getlist(self.request, 'id')
        if ids:
            queryset = queryset.filter(pk__in=ids)

        statuses = splitting_getlist(self.request, 'status')
        if statuses:
            statuses = [status.upper() for status in statuses]
            queryset = queryset.filter(status__in=statuses)

        before = self.request.query_params.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except Exception:
                queryset = queryset.filter(pk=-1)

        return queryset.order_by('-created_on').select_related('org').prefetch_related('urns', 'contacts', 'groups')

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List recent broadcasts",
                    url=reverse('api.v1.broadcasts'),
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
                    url=reverse('api.v1.broadcasts'),
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


class MessageEndpoint(ListAPIMixin, CreateAPIMixin, BaseAPIView):
    """
    This endpoint allows you either list messages on your account using the ```GET``` method.

    ** Note that sending messages using this endpoint is deprecated, you should instead use the
       [broadcasts](/api/v1/broadcasts) endpoint to send new messages. **

    ## Listing Messages

    Returns the message activity for your organization, listing the most recent messages first.

      * **channel** - the id of the channel that sent or received this message (int) (filterable: ```channel``` repeatable)
      * **broadcast** - the broadcast this message is associated with (filterable as ```broadcast``` repeatable)
      * **urn** - the URN of the sender or receiver, depending on direction (string) (filterable: ```urn``` repeatable)
      * **contact** - the UUID of the contact (string) (filterable: ```contact```repeatable )
      * **group_uuids** - the UUIDs of any groups the contact belongs to (string) (filterable: ```group_uuids``` repeatable)
      * **direction** - the direction of the SMS, either ```I``` for incoming messages or ```O``` for outgoing (string) (filterable: ```direction``` repeatable)
      * **archived** - whether this message is archived (boolean) (filterable: ```archived```)
      * **labels** - any labels set on this message (filterable: ```label``` repeatable)
      * **text** - the text of the message received, note this is the logical view, this message may have been received as multiple text messages (string)
      * **created_on** - the datetime when this message was either received by the channel or created (datetime) (filterable: ```before``` and ```after```)
      * **sent_on** - for outgoing messages, the datetime when the channel sent the message (null if not yet sent or an incoming message) (datetime)
      * **flow** - the flow this message is associated with (only filterable as ```flow``` repeatable)
      * **status** - the status of this message, a string one of: (filterable: ```status``` repeatable)

            Q - Message is queued awaiting to be sent
            S - Message has been sent by the channel
            D - Message was delivered to the recipient
            H - Incoming message was handled
            F - Message was not sent due to a failure
            W - Message has been delivered to the channel

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
                "broadcast": 67,
                "contact": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
                "status": "Q",
                "relayer": 5,
                "urn": "tel:+250788123124",
                "direction": "O",
                "archived": false,
                "text": "hello world",
                "created_on": "2013-03-02T17:28:12",
                "sent_on": null,
                "delivered_on": null
            },
            ...

    """
    permission = 'msgs.msg_api'
    model = Msg
    serializer_class = MsgReadSerializer
    write_serializer_class = MsgCreateSerializer
    cache_counts = True

    def render_write_response(self, write_output, context):
        # use a different serializer for created messages

        response_serializer = MsgCreateResultSerializer(instance=write_output, context=context)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def get_queryset(self):
        org = self.request.user.get_org()
        queryset = Msg.all_messages.filter(org=org, contact__is_test=False)

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

        urns = self.request.query_params.getlist('urn', None)
        if urns:
            queryset = queryset.filter(contact__urns__urn__in=urns)

        before = self.request.query_params.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except Exception:
                queryset = queryset.filter(pk=-1)

        channels = self.request.query_params.getlist('channel', None)
        if channels:
            queryset = queryset.filter(channel__id__in=channels)

        contact_uuids = splitting_getlist(self.request, 'contact')
        if contact_uuids:
            queryset = queryset.filter(contact__uuid__in=contact_uuids)

        groups = self.request.query_params.getlist('group', None)  # deprecated, use group_uuids
        if groups:
            queryset = queryset.filter(contact__all_groups__name__in=groups,
                                       contact__all_groups__group_type=ContactGroup.TYPE_USER_DEFINED)

        group_uuids = splitting_getlist(self.request, 'group_uuids')
        if group_uuids:
            queryset = queryset.filter(contact__all_groups__uuid__in=group_uuids,
                                       contact__all_groups__group_type=ContactGroup.TYPE_USER_DEFINED)

        types = splitting_getlist(self.request, 'type')
        if types:
            queryset = queryset.filter(msg_type__in=types)

        labels_included, labels_required, labels_excluded = [], [], []
        for label in self.request.query_params.getlist('label', []):
            if label.startswith('+'):
                labels_required.append(label[1:])
            elif label.startswith('-'):
                labels_excluded.append(label[1:])
            else:
                labels_included.append(label)

        if labels_included:
            queryset = queryset.filter(labels__name__in=labels_included)
        for label in labels_required:
            queryset = queryset.filter(labels__name=label)
        for label in labels_excluded:
            queryset = queryset.exclude(labels__name=label)

        text = self.request.query_params.get('text', None)
        if text:
            queryset = queryset.filter(text__icontains=text)

        flows = splitting_getlist(self.request, 'flow')
        if flows:
            queryset = queryset.filter(steps__run__flow__in=flows)

        broadcasts = splitting_getlist(self.request, 'broadcast')
        if broadcasts:
            queryset = queryset.filter(broadcast__in=broadcasts)

        archived = self.request.query_params.get('archived', None)
        if archived is not None:
            visibility = Msg.VISIBILITY_ARCHIVED if str_to_bool(archived) else Msg.VISIBILITY_VISIBLE
            queryset = queryset.filter(visibility=visibility)
        else:
            queryset = queryset.exclude(visibility=Msg.VISIBILITY_DELETED)

        queryset = queryset.select_related('org', 'contact', 'contact_urn').prefetch_related('labels')
        return queryset.order_by('-created_on').distinct()

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List recent messages",
                    url=reverse('api.v1.messages'),
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
                          dict(name='archived', required=False,
                               help="Filter returned messages based on whether they are archived. ex: Y"),
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
                               help="Only return messages that were received or sent by these channels. (repeatable)  ex: 515,854")]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Send one or more messages",
                    url=reverse('api.v1.messages'),
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


class MessageBulkActionEndpoint(BaseAPIView):
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
    serializer_class = MsgBulkActionSerializer

    def post(self, request, *args, **kwargs):
        user = request.user
        serializer = self.serializer_class(user=user, data=request.data)

        if serializer.is_valid():
            serializer.save()
            return Response('', status=status.HTTP_204_NO_CONTENT)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Update one or more messages",
                    url=reverse('api.v1.message_actions'),
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


class LabelEndpoint(ListAPIMixin, CreateAPIMixin, BaseAPIView):
    """
    ## Listing Message Labels

    A **GET** returns the list of message labels for your organization, in the order of last created.

    * **uuid** - the UUID of the label (string) (filterable: ```uuid``` repeatable)
    * **name** - the name of the label (string) (filterable: ```name```)
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
                    "count": 315
                },
                ...
            ]
        }

    ## Adding a Label

    A **POST** can be used to create a new message label. Don't specify a UUID as this will be generated for you.

    * **name** - the label name (string)

    Example:

        POST /api/v1/labels.json
        {
            "name": "Screened"
        }

    You will receive a label object (with the new UUID) as a response if successful:

        {
            "uuid": "fdd156ca-233a-48c1-896d-a9d594d59b95",
            "name": "Screened",
            "count": 0
        }

    ## Updating a Label

    A **POST** can also be used to update an message label if you do specify it's UUID.

    * **uuid** - the label UUID
    * **name** - the label name (string)

    Example:

        POST /api/v1/labels.json
        {
            "uuid": "fdd156ca-233a-48c1-896d-a9d594d59b95",
            "name": "Checked"
        }

    You will receive the updated label object as a response if successful:

        {
            "uuid": "fdd156ca-233a-48c1-896d-a9d594d59b95",
            "name": "Checked",
            "count": 0
        }
    """
    permission = 'msgs.label_api'
    model = Label
    serializer_class = LabelReadSerializer
    write_serializer_class = LabelWriteSerializer

    def get_queryset(self):
        queryset = self.model.label_objects.filter(org=self.request.user.get_org()).order_by('-pk')

        name = self.request.query_params.get('name', None)
        if name:
            queryset = queryset.filter(name__icontains=name)

        uuids = self.request.query_params.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Message Labels",
                    url=reverse('api.v1.labels'),
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
                    url=reverse('api.v1.labels'),
                    slug='label-update',
                    request='{ "name": "Screened" }')

        spec['fields'] = [dict(name='uuid', required=True,
                               help='The UUID of the message label.  ex: "fdd156ca-233a-48c1-896d-a9d594d59b95"'),
                          dict(name='name', required=False,
                               help='The name of the message label.  ex: "Screened"')]
        return spec


class CallEndpoint(ListAPIMixin, BaseAPIView):
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
    permission = 'channels.channelevent_api'
    model = ChannelEvent
    serializer_class = ChannelEventSerializer
    cache_counts = True

    def get_queryset(self):
        queryset = self.model.objects.filter(org=self.request.user.get_org(), is_active=True).order_by('-created_on')

        ids = splitting_getlist(self.request, 'call')
        if ids:
            queryset = queryset.filter(pk__in=ids)

        before = self.request.query_params.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except Exception:
                queryset = queryset.filter(pk=-1)

        call_types = splitting_getlist(self.request, 'call_type')
        if call_types:
            queryset = queryset.filter(event_type__in=call_types)

        phones = splitting_getlist(self.request, 'phone')
        if phones:
            queryset = queryset.filter(contact__urns__path__in=phones)

        channel = self.request.query_params.get('relayer', None)
        if channel:
            try:
                channel = int(channel)
                queryset = queryset.filter(channel_id=channel)
            except Exception:
                queryset = queryset.filter(pk=-1)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List recent incoming and outgoing Calls",
                    url=reverse('api.v1.calls'),
                    slug='call-list',
                    request="after=2013-01-01T00:00:00.000&phone=%2B250788123123")
        spec['fields'] = [dict(name='call', required=False,
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
                               help="Only return messages that were received or sent by these channels.  ex: 515,854")]

        return spec


class ChannelEndpoint(ListAPIMixin, CreateAPIMixin, DeleteAPIMixin, BaseAPIView):
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
    write_serializer_class = ChannelClaimSerializer

    def destroy(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        if not queryset:
            return Response(status=status.HTTP_404_NOT_FOUND)
        else:
            for channel in queryset:
                channel.release()
            return Response(status=status.HTTP_204_NO_CONTENT)

    def get_queryset(self):
        queryset = self.model.objects.filter(org=self.request.user.get_org(), is_active=True).order_by('-last_seen')

        ids = splitting_getlist(self.request, 'relayer')
        if ids:
            queryset = queryset.filter(pk__in=ids)

        phones = splitting_getlist(self.request, 'phone')
        if phones:
            queryset = queryset.filter(address__in=phones)

        before = self.request.query_params.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(last_seen__lte=before)
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(last_seen__gte=after)
            except Exception:
                queryset = queryset.filter(pk=-1)

        countries = splitting_getlist(self.request, 'country')
        if countries:
            queryset = queryset.filter(country__in=countries)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Android phones",
                    url=reverse('api.v1.channels'),
                    slug='channel-list',
                    request="after=2013-01-01T00:00:00.000&country=RW")
        spec['fields'] = [dict(name='relayer', required=False,
                               help="One or more channel ids to filter by. (repeatable)  ex: 235,124"),
                          dict(name='phone', required=False,
                               help="One or more phone number to filter by. (repeatable)  ex: +250788123123,+250788456456"),
                          dict(name='before', required=False,
                               help="Only return channels which were last seen before this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='after', required=False,
                               help="Only return channels which were last seen after this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='country', required=False,
                               help="Only channels which are active in countries with these country codes. (repeatable) ex: RW")]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Attach a Channel to your account using a claim code",
                    url=reverse('api.v1.channels'),
                    slug='channel-claim',
                    request='{ "claim_code": "AOIFUGQUF", "phone": "+250788123123", "name": "Rwanda MTN Channel" }')

        spec['fields'] = [dict(name='claim_code', required=True,
                               help="The 9 character claim code displayed by the Android application after startup.  ex: FJUQOGIEF"),
                          dict(name='phone', required=True,
                               help="The phone number of the channel.  ex: +250788123123"),
                          dict(name='name', required=False,
                               help="A friendly name you want to assign to this channel.  ex: MTN Rwanda")]
        return spec

    @classmethod
    def get_delete_explorer(cls):
        spec = dict(method="DELETE",
                    title="Delete Android phones",
                    url=reverse('api.v1.channels'),
                    slug='channel-delete',
                    request="after=2013-01-01T00:00:00.000&country=RW")
        spec['fields'] = [dict(name='relayer', required=False,
                               help="Only delete channels with these ids. (repeatable)  ex: 235,124"),
                          dict(name='phone', required=False,
                               help="Only delete channels with these phones numbers. (repeatable)  ex: +250788123123,+250788456456"),
                          dict(name='before', required=False,
                               help="Only delete channels which were last seen before this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='after', required=False,
                               help="Only delete channels which were last seen after this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='country', required=False,
                               help="Only delete channels which are active in countries with these country codes. (repeatable) ex: RW")]

        return spec


class GroupEndpoint(ListAPIMixin, BaseAPIView):
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

    def get_queryset(self):
        queryset = self.model.user_groups.filter(org=self.request.user.get_org(), is_active=True).order_by('-created_on')

        name = self.request.query_params.get('name', None)
        if name:
            queryset = queryset.filter(name__icontains=name)

        uuids = self.request.query_params.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Contact Groups",
                    url=reverse('api.v1.contactgroups'),
                    slug='contactgroup-list',
                    request="")
        spec['fields'] = [dict(name='name', required=False,
                               help="The name of the Contact Group to return.  ex: Reporters"),
                          dict(name='uuid', required=False,
                               help="The UUID of the Contact Group to return. (repeatable) ex: 5f05311e-8f81-4a67-a5b5-1501b6d6496a")]

        return spec


class ContactEndpoint(ListAPIMixin, CreateAPIMixin, DeleteAPIMixin, BaseAPIView):
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
            "blocked": false,
            "failed": false
        }

    ## Updating Contacts

    You can update contacts in the same manner as adding them but we recommend you pass in the UUID for the contact
    as a way of specifying which contact to update. Note that when you pass in the contact UUID and ```urns```, all
    existing URNs will be evaluated against this new set and updated accordingly.

    ## Listing Contacts

    A **GET** returns the list of contacts for your organization, in the order of last activity date. You can return
    only deleted contacts by passing the "?deleted=true" parameter to your call.

    * **uuid** - the unique identifier for this contact (string) (filterable: ```uuid``` repeatable)
    * **name** - the name of this contact (string, optional)
    * **language** - the preferred language of this contact (string, optional)
    * **urns** - the URNs associated with this contact (string array) (filterable: ```urns```)
    * **group_uuids** - the UUIDs of any groups this contact is part of (string array, optional) (filterable: ```group_uuids``` repeatable)
    * **fields** - any contact fields on this contact (JSON, optional)
    * **after** - only contacts which have changed on this date or after (string) ex: 2012-01-28T18:00:00.000
    * **before** - only contacts which have been changed on this date or before (string) ex: 2012-01-28T18:00:00.000

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
    write_serializer_class = ContactWriteSerializer
    cache_counts = True

    def destroy(self, request, *args, **kwargs):
        queryset = self.get_base_queryset(request)

        # to make it harder for users to delete all their contacts by mistake, we require them to filter by UUID or urns
        uuids = request.query_params.getlist('uuid', None)
        urns = request.query_params.getlist('urns', None)

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
                contact.release(request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)

    def get_base_queryset(self, request):
        queryset = self.model.objects.filter(org=request.user.get_org(), is_test=False)

        # if they pass in deleted=true then only return deleted contacts
        if str_to_bool(request.query_params.get('deleted', '')):
            return queryset.filter(is_active=False)
        else:
            return queryset.filter(is_active=True)

    def get_queryset(self):
        queryset = self.get_base_queryset(self.request)

        before = self.request.query_params.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(modified_on__lte=before)
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(modified_on__gte=after)
            except Exception:
                queryset = queryset.filter(pk=-1)

        phones = splitting_getlist(self.request, 'phone')  # deprecated, use urns
        if phones:
            queryset = queryset.filter(urns__path__in=phones, urns__scheme=TEL_SCHEME)

        urns = self.request.query_params.getlist('urns', None)
        if urns:
            queryset = queryset.filter(urns__urn__in=urns)

        groups = self.request.query_params.getlist('group', None)  # deprecated, use group_uuids
        if groups:
            queryset = queryset.filter(all_groups__name__in=groups,
                                       all_groups__group_type=ContactGroup.TYPE_USER_DEFINED)

        group_uuids = self.request.query_params.getlist('group_uuids', None)
        if group_uuids:
            queryset = queryset.filter(all_groups__uuid__in=group_uuids,
                                       all_groups__group_type=ContactGroup.TYPE_USER_DEFINED)

        uuids = self.request.query_params.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        # can't prefetch a custom manager directly, so here we prefetch user groups as new attribute
        user_groups_prefetch = Prefetch('all_groups', queryset=ContactGroup.user_groups.all(), to_attr='prefetched_user_groups')

        return queryset.select_related('org').prefetch_related(user_groups_prefetch).order_by('-modified_on', 'pk')

    def prepare_for_serialization(self, object_list):
        # initialize caches of all contact fields and URNs
        org = self.request.user.get_org()
        Contact.bulk_cache_initialize(org, object_list)

    def get_serializer_context(self):
        """
        So that we only fetch active contact fields once for all contacts
        """
        context = super(BaseAPIView, self).get_serializer_context()
        context['contact_fields'] = ContactField.objects.filter(org=self.request.user.get_org(), is_active=True)
        return context

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Contacts",
                    url=reverse('api.v1.contacts'),
                    slug='contact-list',
                    request="phone=%2B250788123123")
        spec['fields'] = [dict(name='uuid', required=False,
                               help="One or more UUIDs to filter by. (repeatable) ex: 27fb583b-3087-4778-a2b3-8af489bf4a93"),
                          dict(name='urns', required=False,
                               help="One or more URNs to filter by.  ex: tel:+250788123123,twitter:ben"),
                          dict(name='group_uuids', required=False,
                               help="One or more group UUIDs to filter by. (repeatable) ex: 6685e933-26e1-4363-a468-8f7268ab63a9"),
                          dict(name='after', required=False,
                               help="only contacts which have changed on this date or after.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='before', required=False,
                               help="only contacts which have changed on this date or before. ex: 2012-01-28T18:00:00.000")]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add or update a Contact",
                    url=reverse('api.v1.contacts'),
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
                    url=reverse('api.v1.contacts'),
                    slug='contact-delete',
                    request="uuid=27fb583b-3087-4778-a2b3-8af489bf4a93")
        spec['fields'] = [dict(name='uuid', required=False,
                               help="One or more UUIDs to filter by. (repeatable) ex: 27fb583b-3087-4778-a2b3-8af489bf4a93"),
                          dict(name='urns', required=False,
                               help="One or more URNs to filter by.  ex: tel:+250788123123,twitter:ben"),
                          dict(name='group_uuids', required=False,
                               help="One or more group UUIDs to filter by. (repeatable) ex: 6685e933-26e1-4363-a468-8f7268ab63a9")]
        return spec


class FieldEndpoint(ListAPIMixin, CreateAPIMixin, BaseAPIView):
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
    write_serializer_class = ContactFieldWriteSerializer

    def get_queryset(self):
        queryset = self.model.objects.filter(org=self.request.user.get_org(), is_active=True)

        key = self.request.query_params.get('key', None)
        if key:
            queryset = queryset.filter(key__icontains=key)

        return queryset

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Contact Fields",
                    url=reverse('api.v1.contactfields'),
                    slug='contactfield-list',
                    request="")
        spec['fields'] = [dict(name='key', required=False,
                               help="The key of the Contact Field to return.  ex: state")]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add or update a Contact Field",
                    url=reverse('api.v1.contactfields'),
                    slug='contactfield-update',
                    request='{ "key": "nick_name", "label": "Nick name", "value_type": "T" }')

        spec['fields'] = [dict(name='key',
                               help='The unique key of the field, required when updating a field, generated for new fields.  ex: "nick_name"'),
                          dict(name='label', required=False,
                               help='The label of the field.  ex: "Nick name"'),
                          dict(name='value_type', required=False,
                               help='The value type code. ex: T, N, D, S, I')]
        return spec


class ContactBulkActionEndpoint(BaseAPIView):
    """
    ## Bulk Contact Updating

    A **POST** can be used to perform an action on a set of contacts in bulk.

    * **contacts** - either a single contact UUID or a JSON array of up to 100 contact UUIDs (string or array of strings)
    * **action** - the action to perform, a string one of:

            add - Add the contacts to the given group
            remove - Remove the contacts from the given group
            block - Block the contacts
            unblock - Un-block the contacts
            expire - Force expiration of contacts' active flow runs
            archive - Archive all of the contacts' messages
            delete - Permanently delete the contacts

    * **group** - the name of a contact group (string, optional)
    * **group_uuid** - the UUID of a contact group (string, optional)

    Example:

        POST /api/v1/contact_actions.json
        {
            "contacts": ["7acfa6d5-be4a-4bcc-8011-d1bd9dfasff3", "a5901b62-ba76-4003-9c62-72fdacc1b7b8"],
            "action": "add",
            "group": "Testers"
        }

    You will receive an empty response if successful.
    """
    permission = 'contacts.contact_api'
    serializer_class = ContactBulkActionSerializer

    def post(self, request, *args, **kwargs):
        user = request.user
        serializer = self.serializer_class(user=user, data=request.data)

        if serializer.is_valid():
            serializer.save()
            return Response('', status=status.HTTP_204_NO_CONTENT)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Update one or more contacts",
                    url=reverse('api.v1.contact_actions'),
                    slug='contact-actions',
                    request='{ "contacts": ["7acfa6d5-be4a-4bcc-8011-d1bd9dfasff3"], "action": "add", '
                            '"group_uuid": "fdd156ca-233a-48c1-896d-a9d594d59b95" }')

        spec['fields'] = [dict(name='contacts', required=True,
                               help="A JSON array of one or more strings, each a contact UUID."),
                          dict(name='action', required=True,
                               help="One of the following strings: add, remove, block, unblock, expire, archive, delete"),
                          dict(name='group', required=False,
                               help="The name of a contact group if the action is add or remove"),
                          dict(name='label_uuid', required=False,
                               help="The UUID of a contact group if the action is add or remove")]
        return spec


class FlowResultsEndpoint(BaseAPIView):
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
    permission = 'flows.flow_api'

    def get(self, request, *args, **kwargs):
        user = request.user
        org = user.get_org()

        ruleset, contact_field = None, None

        ruleset_id_or_uuid = self.request.query_params.get('ruleset', None)
        if ruleset_id_or_uuid:
            try:
                ruleset = RuleSet.objects.filter(flow__org=org, pk=int(ruleset_id_or_uuid)).first()
            except ValueError:
                ruleset = RuleSet.objects.filter(flow__org=org, uuid=ruleset_id_or_uuid).first()

            if not ruleset:
                return Response(dict(ruleset=["No ruleset found with that UUID or id"]), status=status.HTTP_400_BAD_REQUEST)

        field = self.request.query_params.get('contact_field', None)
        if field:
            contact_field = ContactField.get_by_label(org, field)
            if not contact_field:
                return Response(dict(contact_field=["No contact field found with that label"]), status=status.HTTP_400_BAD_REQUEST)

        if (not ruleset and not contact_field) or (ruleset and contact_field):
            return Response(dict(non_field_errors=["You must specify either a ruleset or contact field"]), status=status.HTTP_400_BAD_REQUEST)

        segment = self.request.query_params.get('segment', None)
        if segment:
            try:
                segment = json.loads(segment)
            except ValueError:
                return Response(dict(segment=["Invalid segment format, must be in JSON format"]), status=status.HTTP_400_BAD_REQUEST)

        if ruleset:
            data = Value.get_value_summary(ruleset=ruleset, segment=segment)
        else:
            data = Value.get_value_summary(contact_field=contact_field, segment=segment)

        return Response(dict(results=data), status=status.HTTP_200_OK)

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="Get summarized results for a RuleSet or Contact Field",
                    url=reverse('api.v1.results'),
                    slug='flow-results',
                    request="")
        spec['fields'] = [dict(name='flow', required=False,
                               help="One or more flow ids to filter by.  ex: 234235,230420"),
                          dict(name='ruleset', required=False,
                               help="One or more rulesets to filter by.  ex: 12412,12451")]
        return spec


class FlowRunEndpoint(ListAPIMixin, CreateAPIMixin, BaseAPIView):
    """
    This endpoint allows you to list and start flow runs.  A run represents a single contact's path through a flow. A
    run is created for each time a contact is started down a flow.

    ## Listing Flow Runs

    By making a ```GET``` request you can list all the flow runs for your organization, filtering them as needed.  Each
    run has the following attributes:

    * **run** - the id of the run (integer) (filterable: ```run``` repeatable)
    * **flow_uuid** - the UUID of the flow (string) (filterable: ```flow_uuid``` repeatable)
    * **contact** - the UUID of the contact this run applies to (string) filterable: ```contact``` repeatable)
    * **group_uuids** - the UUIDs of any groups this contact is part of (string array, optional) (filterable: ```group_uuids``` repeatable)
    * **created_on** - the datetime when this run was started (datetime)
    * **modified_on** - the datetime when this run was last modified (datetime) (filterable: ```before``` and ```after```)
    * **completed** - boolean indicating whether this run has completed the flow (boolean)
    * **expires_on** - the datetime when this run will expire (datetime)
    * **expired_on** - the datetime when this run expired or null if it has not yet expired (datetime or null)
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
                "run": 10150051,
                "flow_uuid": "f5901b62-ba76-4003-9c62-72fdacc1b7b7",
                "contact": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
                "created_on": "2013-03-02T17:28:12",
                "expires_on": "2015-07-08T01:10:43.111Z",
                "expired_on": null
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
                "modified_on": "2013-08-19T19:11:21.088Z"
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
    serializer_class = FlowRunReadSerializer
    write_serializer_class = FlowRunStartSerializer
    cache_counts = True

    def post(self, request, *args, **kwargs):
        user = request.user
        if user.get_org().is_suspended():
            return Response("Sorry, your account is currently suspended. To enable sending messages, please contact support.", status=status.HTTP_400_BAD_REQUEST)
        return super(FlowRunEndpoint, self).post(request, *args, **kwargs)

    def render_write_response(self, write_output, context):
        if write_output:
            response_serializer = FlowRunReadSerializer(instance=write_output, many=True, context=context)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
        return Response(dict(non_field_errors=["All contacts are already started in this flow, "
                                               "use restart_participants to force them to restart in the flow"]),
                        status=status.HTTP_400_BAD_REQUEST)

    def get_queryset(self):
        org = self.request.user.get_org()
        queryset = self.model.objects.all()

        contact_uuids = splitting_getlist(self.request, 'contact')
        contact_phones = splitting_getlist(self.request, 'phone')  # deprecated

        # subquery on contacts to avoid join - if we're querying for specific contacts
        if contact_uuids or contact_phones:
            include_contacts = Contact.objects.filter(org=org, is_test=False, is_active=True)

            if contact_uuids:
                include_contacts = include_contacts.filter(uuid__in=contact_uuids)

            if contact_phones:
                include_contacts = include_contacts.filter(urns__path__in=contact_phones)

            queryset = queryset.filter(contact__in=include_contacts)
        else:
            test_contacts = Contact.objects.filter(org=org, is_test=True)
            queryset = queryset.exclude(contact__in=test_contacts)

        # subquery on flows to avoid join
        include_flows = Flow.objects.filter(org=org, is_active=True)

        flow_ids = splitting_getlist(self.request, 'flow')  # deprecated, use flow_uuid
        if flow_ids:
            include_flows = include_flows.filter(pk__in=flow_ids)

        flow_uuids = splitting_getlist(self.request, 'flow_uuid')
        if flow_uuids:
            include_flows = include_flows.filter(uuid__in=flow_uuids)

        # if we are filtering by flows, do so
        if flow_ids or flow_uuids:
            queryset = queryset.filter(flow__in=include_flows)

        # otherwise, filter by org
        else:
            queryset = queryset.filter(org=org)

        # other queries on the runs themselves...
        runs = splitting_getlist(self.request, 'run')
        if runs:
            queryset = queryset.filter(pk__in=runs)

        before = self.request.query_params.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(modified_on__lte=before)
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(modified_on__gte=after)
            except Exception:
                queryset = queryset.filter(pk=-1)

        # it's faster to filter by contact group using a join than a subquery - especially for larger groups

        groups = splitting_getlist(self.request, 'group')  # deprecated, use group_uuids
        if groups:
            queryset = queryset.filter(contact__all_groups__name__in=groups,
                                       contact__all_groups__group_type=ContactGroup.TYPE_USER_DEFINED)

        group_uuids = splitting_getlist(self.request, 'group_uuids')
        if group_uuids:
            queryset = queryset.filter(contact__all_groups__uuid__in=group_uuids,
                                       contact__all_groups__group_type=ContactGroup.TYPE_USER_DEFINED)

        rulesets_prefetch = Prefetch('flow__rule_sets',
                                     queryset=RuleSet.objects.exclude(label=None).order_by('pk'),
                                     to_attr='ruleset_prefetch')

        # use prefetch rather than select_related for foreign keys flow/contact to avoid joins
        queryset = queryset.prefetch_related(
            'flow',
            'contact',
            rulesets_prefetch,
            Prefetch('steps', queryset=FlowStep.objects.order_by('arrived_on')),
            Prefetch('steps__messages', queryset=Msg.all_messages.only('broadcast', 'text').order_by('created_on')),
            Prefetch('steps__broadcasts', queryset=Broadcast.objects.only('text').order_by('created_on')),
        )

        return queryset.order_by('-modified_on')

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Flow Runs",
                    url=reverse('api.v1.runs'),
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
                               help="Only return runs which were modified before this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='after', required=False,
                               help="Only return runs which were modified after this date.  ex: 2012-01-28T18:00:00.000")]

        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add one or more contacts to a Flow",
                    url=reverse('api.v1.runs'),
                    slug='run-post',
                    request='{ "flow_uuid":"f5901b62-ba76-4003-9c62-72fdacc1b7b7" , "phone": ["+250788222222", "+250788111111"], "extra": { "item_id": "ONEZ", "item_price":"$3.99" } }')

        spec['fields'] = [dict(name='flow_uuid', required=True,
                               help="The uuid of the flow to start the contact(s) on, the flow cannot be archived"),
                          dict(name='phone', required=True,
                               help="A JSON array of one or more strings, each a phone number in E164 format"),
                          dict(name='contact', required=False,
                               help="A JSON array of one or more strings, each a contact UUID"),
                          dict(name='extra', required=False,
                               help="A dictionary of key/value pairs to include as the @extra parameters in the flow (max of twenty values of 255 chars or less)")]
        return spec


class CampaignEndpoint(ListAPIMixin, CreateAPIMixin, BaseAPIView):
    """
    ## Adding or Updating a Campaign

    You can add a new campaign to your account, or update the fields on a campaign by sending a **POST** request to this
    URL with the following data:

    * **uuid** - the UUID of the campaign (string, optional, only include if updating an existing campaign)
    * **name** - the full name of the campaign (string, required)
    * **group_uuid** - the UUID of the contact group this campaign will be run against (string, required)

    Example:

        POST /api/v1/campaigns.json
        {
            "name": "Reminders",
            "group_uuid": "7ae473e8-f1b5-4998-bd9c-eb8e28c92fa9"
        }

    You will receive a campaign object as a response if successful:

        {
            "uuid": "f14e4ff0-724d-43fe-a953-1d16aefd1c00",
            "name": "Reminders",
            "group_uuid": "7ae473e8-f1b5-4998-bd9c-eb8e28c92fa9",
            "created_on": "2013-08-19T19:11:21.088Z"
        }

    ## Listing Campaigns

    You can retrieve the campaigns for your organization by sending a ```GET``` to the same endpoint, listing the
    most recently created campaigns first.

      * **uuid** - the UUID of the campaign (string) (filterable: ```uuid``` repeatable)
      * **name** - the name of this campaign (string)
      * **group_uuid** - the UUID of the group this campaign operates on (string)
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
                "uuid": "f14e4ff0-724d-43fe-a953-1d16aefd1c00",
                "name": "Reminders",
                "group_uuid": "7ae473e8-f1b5-4998-bd9c-eb8e28c92fa9",
                "created_on": "2013-08-19T19:11:21.088Z"
            },
            ...
        }

    """
    permission = 'campaigns.campaign_api'
    model = Campaign
    serializer_class = CampaignReadSerializer
    write_serializer_class = CampaignWriteSerializer

    def get_queryset(self):
        queryset = Campaign.get_campaigns(self.request.user.get_org())

        uuids = self.request.query_params.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        ids = splitting_getlist(self.request, 'campaign')  # deprecated, use uuid
        if ids:
            queryset = queryset.filter(pk__in=ids)

        before = self.request.query_params.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except Exception:
                queryset = queryset.filter(pk=-1)

        return queryset.select_related('group').order_by('-created_on')

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Campaigns",
                    url=reverse('api.v1.campaigns'),
                    slug='campaign-list',
                    request="after=2013-01-01T00:00:00.000")
        spec['fields'] = [dict(name='uuid', required=False,
                               help="One or more campaign UUIDs to filter by. (repeatable)  ex: f14e4ff0-724d-43fe-a953-1d16aefd1c00"),
                          dict(name='before', required=False,
                               help="Only return flows which were created before this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='after', required=False,
                               help="Only return flows which were created after this date.  ex: 2012-01-28T18:00:00.000")]
        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add or update a Campaign",
                    url=reverse('api.v1.campaigns'),
                    slug='campaign-update',
                    request='{ "name": "Reminders", "group_uuid": "7ae473e8-f1b5-4998-bd9c-eb8e28c92fa9" }')

        spec['fields'] = [dict(name='uuid', required=False,
                               help="The UUID of the campaign to update. (optional, new campaign will be created if left out)  ex: f14e4ff0-724d-43fe-a953-1d16aefd1c00"),
                          dict(name='name', required=True,
                               help='The name of the campaign.  ex: "Reminders"'),
                          dict(name='group_uuid', required=True,
                               help='The UUID of the contact group the campaign should operate against.  ex: "7ae473e8-f1b5-4998-bd9c-eb8e28c92fa9"')]
        return spec


class CampaignEventEndpoint(ListAPIMixin, CreateAPIMixin, DeleteAPIMixin, BaseAPIView):
    """
    ## Adding or Updating Campaign Events

    You can add a new event to your campaign, or update the fields on an event by sending a **POST** request to this
    URL with the following data:

    * **uuid** - the UUID of the event (string, optional, only include if updating an existing campaign)
    * **campaign_uuid** - the UUID of the campaign this event should be part of (string, only include when creating new events)
    * **relative_to** - the field that this event will be relative to for the contact (string, name of the Contact field, required)
    * **offset** - the offset from our contact field (positive or negative integer, required)
    * **unit** - the unit for our offset, M (minutes), H (hours), D (days), W (weeks) (string, required)
    * **delivery_hour** - the hour of the day to deliver the message (integer 0-24, -1 indicates send at the same hour as the Contact Field)
    * **message** - the message to send to the contact (string, required if flow id is not included)
    * **flow_uuid** - the UUID of the flow to start the contact down (string, required if message is null)

    Example:

        POST /api/v1/events.json
        {
            "campaign_uuid": "f14e4ff0-724d-43fe-a953-1d16aefd1c00",
            "relative_to": "Last Hit",
            "offset": 160,
            "unit": "W",
            "delivery_hour": -1,
            "message": "Feeling sick and helpless, lost the compass where self is."
        }

    You will receive an event object as a response if successful:

        {
            "uuid": "6a6d7531-6b44-4c45-8c33-957ddd8dfabc",
            "campaign_uuid": "f14e4ff0-724d-43fe-a953-1d16aefd1c00",
            "relative_to": "Last Hit",
            "offset": 160,
            "unit": "W",
            "delivery_hour": -1,
            "message": "Feeling sick and helpless, lost the compass where self is."
            "flow_uuid": null,
            "created_on": "2013-08-19T19:11:21.088Z"
        }

    ## Listing Events

    You can retrieve the campaign events for your organization by sending a ```GET``` to the same endpoint, listing the
    most recently created campaigns first.

      * **uuid** - only return events with these UUIDs (string) (filterable: ```uuid``` repeatable)
      * **campaign_uuid** - the UUID of the campaign (string) (filterable: ```campaign_uuid``` repeatable)
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
                "uuid": "6a6d7531-6b44-4c45-8c33-957ddd8dfabc",
                "campaign_uuid": "f14e4ff0-724d-43fe-a953-1d16aefd1c00",
                "relative_to": "Last Hit",
                "offset": 180,
                "unit": "W",
                "delivery_hour": -1,
                "message": "If I can be an example of being sober, then I can be an example of starting over.",
                "flow_uuid": null,
                "created_on": "2013-08-19T19:11:21.088Z"
            },
            ...
        }

    ## Removing Events

    A **DELETE** to the endpoint removes all matching events from your account.  You can filter the list of events to
    remove using the following attributes

    * **uuid** - remove only events with these UUIDs (comma separated string)
    * **campaign_uuid** - remove only events which are part of this campaign (comma separated string)

    Example:

        DELETE /api/v1/events.json?uuid=6a6d7531-6b44-4c45-8c33-957ddd8dfabc

    You will receive either a 404 response if no matching events were found, or a 204 response if one or more events
    was removed.
    """
    permission = 'campaigns.campaignevent_api'
    model = CampaignEvent
    serializer_class = CampaignEventReadSerializer
    write_serializer_class = CampaignEventWriteSerializer

    def destroy(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        if not queryset:
            return Response(status=status.HTTP_404_NOT_FOUND)
        else:
            queryset.update(is_active=False)
            return Response(status=status.HTTP_204_NO_CONTENT)

    def get_queryset(self):
        queryset = self.model.objects.filter(campaign__org=self.request.user.get_org(), is_active=True)

        uuids = splitting_getlist(self.request, 'uuid')
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        campaign_uuids = splitting_getlist(self.request, 'campaign_uuid')
        if campaign_uuids:
            queryset = queryset.filter(campaign__uuid__in=campaign_uuids)

        ids = splitting_getlist(self.request, 'event')  # deprecated, use uuid
        if ids:
            queryset = queryset.filter(pk__in=ids)

        campaign_ids = splitting_getlist(self.request, 'campaign')  # deprecated, use campaign_uuid
        if campaign_ids:
            queryset = queryset.filter(campaign__pk__in=campaign_ids)

        before = self.request.query_params.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except Exception:
                queryset = queryset.filter(pk=-1)

        return queryset.select_related('campaign', 'flow').order_by('-created_on')

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Campaign Events",
                    url=reverse('api.v1.campaignevents'),
                    slug='campaignevent-list',
                    request="after=2013-01-01T00:00:00.000")

        spec['fields'] = [dict(name='uuid', required=False,
                               help="One or more event UUIDs to filter by. (repeatable) ex: 6a6d7531-6b44-4c45-8c33-957ddd8dfabc"),
                          dict(name='campaign_uuid', required=False,
                               help="One or more campaign UUIDs to filter by. (repeatable) ex: f14e4ff0-724d-43fe-a953-1d16aefd1c00"),
                          dict(name='before', required=False,
                               help="Only return flows which were created before this date.  ex: 2012-01-28T18:00:00.000"),
                          dict(name='after', required=False,
                               help="Only return flows which were created after this date.  ex: 2012-01-28T18:00:00.000")]
        return spec

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Add or update a Campaign Event",
                    url=reverse('api.v1.campaignevents'),
                    slug='campaignevent-update',
                    request='{ "campaign_uuid": "f14e4ff0-724d-43fe-a953-1d16aefd1c00", "relative_to": "Last Hit", "offset": 180, "unit": "W", "delivery_hour": -1, "message": "If I can be an example of being sober, then I can be an example of starting over."}')

        spec['fields'] = [dict(name='uuid', required=False,
                               help="The UUID of the event to update. (optional, new event will be created if left out)  ex: 6a6d7531-6b44-4c45-8c33-957ddd8dfab"),
                          dict(name='campaign_uuid', required=False,
                               help="The UUID of the campaign this event is part of. (optional, only used when creating a new campaign)  ex: f14e4ff0-724d-43fe-a953-1d16aefd1c00"),
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
                          dict(name='flow_uuid', required=False,
                               help='If not message is included, then the UUID of the flow that the contact should start when this event is triggered (string)  ex: 6db50de7-2d20-4cce-b0dd-3f38b7a52ff9')]
        return spec

    @classmethod
    def get_delete_explorer(cls):
        spec = dict(method="DELETE",
                    title="Delete a Campaign Event from a Campaign",
                    url=reverse('api.v1.campaignevents'),
                    slug='campaignevent-delete',
                    request="uuid=6a6d7531-6b44-4c45-8c33-957ddd8dfabc")

        spec['fields'] = [dict(name='uuid', required=False,
                               help="Only delete events with these UUIDs. (repeatable) ex: 6a6d7531-6b44-4c45-8c33-957ddd8dfabc"),
                          dict(name='campaign_uuid', required=False,
                               help="Only delete events that are part of these campaigns. ex: f14e4ff0-724d-43fe-a953-1d16aefd1c00")]
        return spec


class BoundaryEndpoint(ListAPIMixin, BaseAPIView):
    """
    This endpoint allows you to list the administrative boundaries for the country associated with your organization
    along with the simplified gps geometry for those boundaries in GEOJSON format.

    ## Listing Boundaries

    Returns the boundaries for your organization. You can return just the names of the boundaries and their aliases,
    without any coordinate information by passing "?aliases=true".

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

    def get_queryset(self):

        org = self.request.user.get_org()
        if not org.country:
            return []

        queryset = org.country.get_descendants(include_self=True).order_by('level', 'name')

        if self.request.GET.get('aliases'):
            queryset = queryset.prefetch_related(
                Prefetch('aliases', queryset=BoundaryAlias.objects.filter(org=org).order_by('name')),
            )

        return queryset.select_related('parent')

    def get_serializer_class(self):
        if self.request.GET.get('aliases'):
            return AliasSerializer
        else:
            return BoundarySerializer

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List the Administrative Boundaries",
                    url=reverse('api.v1.boundaries'),
                    slug='boundary-list',
                    request="")
        spec['fields'] = []

        return spec


class FlowDefinitionEndpoint(BaseAPIView, CreateAPIMixin):
    """
    This endpoint returns a flow definition given a flow uuid. Posting to it allows creation
    or updating of existing flows. This endpoint should be considered to only have alpha-level
    support and is subject to modification or removal.

    ## Getting a flow definition

    Returns the flow definition for the given flow.

      * **uuid** - the UUID of the flow (string)

    Example:

        GET /api/v1/flow_definition.json?uuid=f14e4ff0-724d-43fe-a953-1d16aefd1c0b

    Response is a flow definition

        {
          metadata: {
            "name": "Water Point Survey",
            "uuid": "f14e4ff0-724d-43fe-a953-1d16aefd1c0b",
            "saved_on": "2015-09-23T00:25:50.709164Z",
            "revision":28,
            "expires":7880,
            "id":12712,
          },
          "version": 7,
          "flow_type": "S",
          "base_language": "eng",
          "entry": "87929095-7d13-4003-8ee7-4c668b736419",
          "action_sets": [
            {
              "y": 0,
              "x": 100,
              "destination": "32d415f8-6d31-4b82-922e-a93416d5aa0a",
              "uuid": "87929095-7d13-4003-8ee7-4c668b736419",
              "actions": [
                {
                  "msg": {
                    "eng": "What is your name?"
                  },
                  "type": "reply"
                }
              ]
            },
            ...
          ],
          "rule_sets": [
            {
              "uuid": "32d415f8-6d31-4b82-922e-a93416d5aa0a",
              "webhook_action": null,
              "rules": [
                {
                  "test": {
                    "test": "true",
                    "type": "true"
                  },
                  "category": {
                    "eng": "All Responses"
                  },
                  "destination": null,
                  "uuid": "5fa6e9ae-e78e-4e38-9c66-3acf5e32fcd2",
                  "destination_type": null
                }
              ],
              "webhook": null,
              "ruleset_type": "wait_message",
              "label": "Name",
              "operand": "@step.value",
              "finished_key": null,
              "y": 162,
              "x": 62,
              "config": {}
            },
            ...
          ]
          }
        }

    ## Saving a flow definition

    By making a ```POST``` request to the endpoint you can create or update an existing flow

    * **metadata** - contains the name and uuid (optional) for the flow
    * **version** - the flow spec version for the definition being submitted
    * **base_language** - the default language code to use for the flow
    * **flow_type** - the type of the flow (F)low, (V)oice, (S)urvey
    * **action_sets** - the actions in the flow
    * **rule_sets** - the rules in the flow
    * **entry** - the uuid for the action_set or rule_set the flow starts at

    Example:

        POST /api/v1/flow_definition.json
        {
          "metadata": {
            "uuid": "f14e4ff0-724d-43fe-a953-1d16aefd1c00",
            "name": "Registration Flow"
          },
          "version": 7,
          "flow_type": "S",
          "base_language": "eng",
          "entry": "87929095-7d13-4003-8ee7-4c668b736419",
          "action_sets": [
            {
              "y": 0,
              "x": 100,
              "destination": "32d415f8-6d31-4b82-922e-a93416d5aa0a",
              "uuid": "87929095-7d13-4003-8ee7-4c668b736419",
              "actions": [
                {
                  "msg": {
                    "eng": "What is your name?"
                  },
                  "type": "reply"
                }
              ]
            },
            ...
          ],
          "rule_sets": [
            {
              "uuid": "32d415f8-6d31-4b82-922e-a93416d5aa0a",
              "webhook_action": null,
              "rules": [
                {
                  "test": {
                    "test": "true",
                    "type": "true"
                  },
                  "category": {
                    "eng": "All Responses"
                  },
                  "destination": null,
                  "uuid": "5fa6e9ae-e78e-4e38-9c66-3acf5e32fcd2",
                  "destination_type": null
                }
              ],
              "webhook": null,
              "ruleset_type": "wait_message",
              "label": "Name",
              "operand": "@step.value",
              "finished_key": null,
              "y": 162,
              "x": 62,
              "config": {}
            },
            ...
          ]
        }

    """
    permission = 'flows.flow_api'
    model = Flow
    write_serializer_class = FlowWriteSerializer

    def get(self, request, *args, **kwargs):

        uuid = request.GET.get('uuid')
        flow = Flow.objects.filter(org=self.request.user.get_org(), is_active=True, uuid=uuid).first()

        if flow:
            # make sure we have the latest format
            flow.ensure_current_version()
            return Response(flow.as_json(), status=status.HTTP_200_OK)
        else:
            return Response(dict(error="Invalid flow uuid"), status=status.HTTP_400_BAD_REQUEST)

    def render_write_response(self, flow, context):
        return Response(flow.as_json(), status=status.HTTP_201_CREATED)


class FlowEndpoint(ListAPIMixin, BaseAPIView):
    """
    This endpoint allows you to list all the active flows on your account using the ```GET``` method.

    ## Listing Flows

    Returns the flows for your organization, listing the most recent flows first.

      * **uuid** - the UUID of the flow (string) (filterable: ```uuid``` repeatable)
      * **name** - the name of the flow (string)
      * **archived** - whether this flow is archived (boolean) (filterable: ```archived```)
      * **labels** - the labels for this flow (string array) (filterable: ```label``` repeatable)
      * **created_on** - the datetime when this flow was created (datetime) (filterable: ```before``` and ```after```)
      * **expires** - the time (in minutes) when this flow's inactive contacts will expire (integer)
      * **runs** - the total number of runs for this flow (integer)
      * **completed_runs** - the number of completed runs for this flow (integer)
      * **rulesets** - the rulesets on this flow, including their node UUID, ruleset type, and label

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
                "expires": 720,
                "name": "Thrift Shop Status",
                "labels": [ "Polls" ],
                "runs": 3,
                "completed_runs": 0,
                "rulesets": [
                   {
                    "id": 17122,
                    "node": "fe594710-68fc-4cb5-bd85-c0c77e4caa45",
                    "ruleset_type": "wait_message",
                    "label": "Age"
                   },
                   {
                    "id": 17128,
                    "node": "fe594710-68fc-4cb5-bd85-c0c77e4caa45",
                    "ruleset_type": "wait_message",
                    "label": "Gender"
                   }
                ]
            },
            ...
        }

    """
    permission = 'flows.flow_api'
    model = Flow
    serializer_class = FlowReadSerializer

    def get_queryset(self):
        queryset = self.model.objects.filter(org=self.request.user.get_org(), is_active=True).order_by('-created_on')

        uuids = self.request.query_params.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        ids = self.request.query_params.getlist('flow', None)  # deprecated, use uuid
        if ids:
            queryset = queryset.filter(pk__in=ids)

        before = self.request.query_params.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except Exception:
                queryset = queryset.filter(pk=-1)

        label = self.request.query_params.getlist('label', None)
        if label:
            queryset = queryset.filter(labels__name__in=label)

        archived = self.request.query_params.get('archived', None)
        if archived is not None:
            queryset = queryset.filter(is_archived=str_to_bool(archived))

        flow_type = self.request.query_params.getlist('type', None)
        if flow_type:
            queryset = queryset.filter(flow_type__in=flow_type)

        return queryset.prefetch_related('labels')

    @classmethod
    def get_read_explorer(cls):
        spec = dict(method="GET",
                    title="List Flows",
                    url=reverse('api.v1.flows'),
                    slug='flow-list',
                    request="after=2013-01-01T00:00:00.000")
        spec['fields'] = [dict(name='flow', required=False,
                               help="One or more flow ids to filter by. (repeatable) ex: 234235,230420"),
                          dict(name='before', required=False,
                               help="Only return flows which were created before this date. ex: 2012-01-28T18:00:00.000"),
                          dict(name='after', required=False,
                               help="Only return flows which were created after this date. ex: 2012-01-28T18:00:00.000"),
                          dict(name='label', required=False,
                               help="Only return flows with this label. (repeatable) ex: Polls"),
                          dict(name='archived', required=False,
                               help="Filter returned flows based on whether they are archived. ex: Y")]

        return spec


class AssetEndpoint(BaseAPIView):
    """
    This endpoint allows you to fetch assets associated with your account using the ```GET``` method.
    """
    def get(self, request, *args, **kwargs):
        type_name = request.GET.get('type')
        identifier = request.GET.get('identifier')
        if not type_name or not identifier:
            return HttpResponseBadRequest("Must provide type and identifier")

        if type_name not in AssetType.__members__:
            return HttpResponseBadRequest("Invalid asset type: %s" % type_name)

        return handle_asset_request(request.user, AssetType[type_name], identifier)


class OrgEndpoint(BaseAPIView):
    """
    ## Viewing Current Organization

    A **GET** returns the details of your organization. There are no parameters.

    Example:

        GET /api/v1/org.json

    Response containing your organization:

        {
            "name": "Nyaruka",
            "country": "RW",
            "languages": ["eng", "fre"],
            "primary_language": "eng",
            "timezone": "Africa/Kigali",
            "date_style": "day_first",
            "anon": false
        }
    """
    permission = 'orgs.org_api'

    def get(self, request, *args, **kwargs):
        org = request.user.get_org()

        data = dict(name=org.name,
                    country=org.get_country_code(),
                    languages=[l.iso_code for l in org.languages.order_by('iso_code')],
                    primary_language=org.primary_language.iso_code if org.primary_language else None,
                    timezone=org.timezone,
                    date_style=('day_first' if org.get_dayfirst() else 'month_first'),
                    anon=org.is_anon)

        return Response(data, status=status.HTTP_200_OK)

    @classmethod
    def get_read_explorer(cls):
        return dict(method="GET", title="View Current Org", url=reverse('api.v1.org'), slug='org-read', request="")


class FlowStepEndpoint(CreateAPIMixin, BaseAPIView):
    """
    This endpoint allows you to create flow runs and steps.

    ## Creating flow steps

    By making a ```POST``` request to the endpoint you can add a new steps to a flow run.

    * **flow** - the UUID of the flow (string)
    * **revision** - the revision of the flow that was executed (integer)
    * **contact** - the UUID of the contact (string)
    * **steps** - the new step objects (array of objects)
    * **started** - the datetime when the run was started
    * **completed** - whether the run is complete

    Example:

        POST /api/v1/steps.json
        {
            "flow": "f5901b62-ba76-4003-9c62-72fdacc1b7b7",
            "revision": 2,
            "contact": "cf85cb74-a4e4-455b-9544-99e5d9125cfd",
            "completed": true,
            "started": "2015-09-23T17:59:47.572Z"
            "steps": [
                {
                    "node": "32cf414b-35e3-4c75-8a78-d5f4de925e13",
                    "arrived_on": "2015-08-25T11:59:30.088Z",
                    "actions": [{"msg":"Hi Joe","type":"reply"}],
                    "errors": []
                }
            ]
        }

    Response is the updated or created flow run.
    """
    permission = 'flows.flow_api'
    model = FlowRun
    serializer_class = FlowRunReadSerializer
    write_serializer_class = FlowRunWriteSerializer

    def render_write_response(self, write_output, context):
        response_serializer = FlowRunReadSerializer(instance=write_output, context=context)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @classmethod
    def get_write_explorer(cls):
        spec = dict(method="POST",
                    title="Create or update a flow run with new steps",
                    url=reverse('api.v1.steps'),
                    slug='step-post',
                    request='{ "contact": "cf85cb74-a4e4-455b-9544-99e5d9125cfd", "flow": "f5901b62-ba76-4003-9c62-72fdacc1b7b7", "steps": [{"node": "32cf414b-35e3-4c75-8a78-d5f4de925e13", "arrived_on": "2015-08-25T11:59:30.088Z", "actions": [{"msg":"Hi Joe","type":"reply"}], "errors": []}] }')

        spec['fields'] = [dict(name='contact', required=True,
                               help="The UUID of the contact"),
                          dict(name='flow', required=True,
                               help="The UUID of the flow"),
                          dict(name='started', required=True,
                               help='Datetime when the flow started'),
                          dict(name='completed', required=True,
                               help='Boolean whether the run is complete or not'),
                          dict(name='steps', required=True,
                               help="A JSON array of one or objects, each a flow step")]
        return spec
