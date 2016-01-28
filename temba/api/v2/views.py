from __future__ import absolute_import, unicode_literals

from django.db.models import Prefetch, Q
from django.db.transaction import non_atomic_requests
from rest_framework import generics, mixins, pagination
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from smartmin.views import SmartTemplateView
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactURN, ContactGroup, ContactField
from temba.flows.models import Flow, FlowRun, FlowStep
from temba.msgs.models import Msg, Label, SystemLabel, DELETED
from temba.orgs.models import Org
from temba.utils import str_to_bool, json_date_to_datetime
from .serializers import ContactReadSerializer, FlowRunReadSerializer, MsgReadSerializer
from ..models import ApiPermission, SSLPermission
from ..support import InvalidQueryError


@api_view(['GET'])
@permission_classes((SSLPermission, IsAuthenticated))
def api(request, format=None):
    """
    This is the **under-development** API v2. Everything in this version of the API is subject to change. We strongly
    recommend that most users stick with the existing [API v1](/api/v1) for now.

    The following endpoints are provided:

     * [/api/v2/contacts](/api/v2/contacts) - to list contacts
     * [/api/v2/messages](/api/v2/messages) - to list messages
     * [/api/v2/runs](/api/v2/runs) - to list flow runs

    You may wish to use the [API Explorer](/api/v2/explorer) to interactively experiment with the API.
    """
    return Response({
        'contacts': reverse('api.v2.contacts', request=request),
        'messages': reverse('api.v2.messages', request=request),
        'runs': reverse('api.v2.runs', request=request),
    })


class ApiExplorerView(SmartTemplateView):
    """
    Explorer view which lets users experiment with endpoints against their own data
    """
    template_name = "api/v2/api_explorer.html"

    def get_context_data(self, **kwargs):
        context = super(ApiExplorerView, self).get_context_data(**kwargs)
        context['endpoints'] = [
            ContactsEndpoint.get_read_explorer(),
            MessagesEndpoint.get_read_explorer(),
            RunsEndpoint.get_read_explorer()
        ]
        return context


class CreatedOnCursorPagination(pagination.CursorPagination):
    ordering = '-created_on'


class ModifiedOnCursorPagination(pagination.CursorPagination):
    ordering = '-modified_on'


class BaseAPIView(generics.GenericAPIView):
    """
    Base class of all our API endpoints
    """
    permission_classes = (SSLPermission, ApiPermission)

    @non_atomic_requests
    def dispatch(self, request, *args, **kwargs):
        return super(BaseAPIView, self).dispatch(request, *args, **kwargs)


class ListAPIMixin(mixins.ListModelMixin):
    """
    Mixin for any endpoint which returns a list of objects from a GET request
    """
    throttle_scope = 'v2'
    model = None
    model_manager = 'objects'

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        if not kwargs.get('format', None):
            # if this is just a request to browse the endpoint docs, don't make a query
            return Response([])
        else:
            return super(ListAPIMixin, self).list(request, *args, **kwargs)

    def get_queryset(self):
        org = self.request.user.get_org()

        return getattr(self.model, self.model_manager).filter(org=org)

    def filter_before_after(self, queryset, field):
        """
        Filters the queryset by the before/after params if are provided
        """
        before = self.request.query_params.get('before')
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(**{field + '__lte': before})
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after')
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(**{field + '__gte': after})
            except Exception:
                queryset = queryset.filter(pk=-1)

        return queryset

    def paginate_queryset(self, queryset):
        page = super(ListAPIMixin, self).paginate_queryset(queryset)

        # give views a chance to prepare objects for serialization
        self.prepare_for_serialization(page)

        return page

    def prepare_for_serialization(self, page):
        """
        Views can override this to do things like bulk cache initialization of result objects
        """
        pass


# ============================================================
# Endpoints (A-Z)
# ============================================================

class ContactsEndpoint(ListAPIMixin, BaseAPIView):
    """
    ## Listing Contacts

    A **GET** returns the list of contacts for your organization, in the order of last activity date. You can return
    only deleted contacts by passing the "deleted=true" parameter to your call.

    * **uuid** - the unique identifier of the contact (string), filterable as `uuid`.
    * **name** - the name of the contact (string).
    * **language** - the preferred language of the contact (string).
    * **urns** - the URNs associated with the contact (string array), filterable as `urn`.
    * **groups** - the UUIDs of any groups the contact is part of (array of objects), filterable as `group` with group name or UUID.
    * **fields** - any contact fields on this contact (dictionary).
    * **created_on** - when this contact was created (datetime).
    * **modified_on** - when this contact was last modified (datetime), filterable as `before` and `after`.

    Example:

        GET /api/v1/contacts.json

    Response containing the contacts for your organization:

        {
            "next": null,
            "previous": null,
            "results": [
            {
                "uuid": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
                "name": "Ben Haggerty",
                "language": null,
                "urns": ["tel:+250788123123"],
                "groups": [{"name": "Customers", "uuid": "5a4eb79e-1b1f-4ae3-8700-09384cca385f"}],
                "fields": {
                  "nickname": "Macklemore",
                  "side_kick": "Ryan Lewis"
                }
                "created_on": "2015-11-11T13:05:57.457742Z",
                "modified_on": "2015-11-11T13:05:57.576056Z"
            }]
        }
    """
    permission = 'contacts.contact_api'
    model = Contact
    serializer_class = ContactReadSerializer
    pagination_class = ModifiedOnCursorPagination

    def filter_queryset(self, queryset):
        params = self.request.query_params
        org = self.request.user.get_org()

        deleted_only = str_to_bool(params.get('deleted'))
        queryset = queryset.filter(is_test=False, is_active=(not deleted_only))

        # filter by UUID (optional)
        uuid = params.get('uuid')
        if uuid:
            queryset = queryset.filter(uuid=uuid)

        # filter by URN (optional)
        urn = params.get('urn')
        if urn:
            queryset = queryset.filter(urns__urn=urn)

        # filter by group name/uuid (optional)
        group_ref = params.get('group')
        if group_ref:
            group = ContactGroup.user_groups.filter(org=org, is_active=True).filter(Q(uuid=group_ref) | Q(name=group_ref)).first()
            if group:
                queryset = queryset.filter(all_groups=group)
            else:
                queryset = queryset.filter(pk=-1)

        # use prefetch rather than select_related for foreign keys to avoid joins
        queryset = queryset.prefetch_related(
            Prefetch('org', queryset=Org.objects.only('is_anon')),
            Prefetch('all_groups', queryset=ContactGroup.user_groups.only('uuid', 'name'), to_attr='prefetched_user_groups')
        )

        return self.filter_before_after(queryset, 'modified_on')

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
        return {
            'method': "GET",
            'title': "List Contacts",
            'url': reverse('api.v2.contacts'),
            'slug': 'contact-list',
            'request': "urn=tel%3A%2B250788123123",
            'fields': [
                {'name': "uuid", 'required': False, 'help': "A contact UUID to filter by. ex: 09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"},
                {'name': "urn", 'required': False, 'help': "A contact URN to filter by. ex: tel:+250788123123"},
                {'name': "group", 'required': False, 'help': "A group name or UUID to filter by. ex: Customers"},
                {'name': 'before', 'required': False, 'help': "Only return contacts modified before this date, ex: 2015-01-28T18:00:00.000"},
                {'name': 'after', 'required': False, 'help': "Only return contacts modified after this date, ex: 2015-01-28T18:00:00.000"}
            ]
        }


class MessagesEndpoint(ListAPIMixin, BaseAPIView):
    """
    This endpoint allows you to fetch messages.

    ## Listing Messages

    By making a ```GET``` request you can list all the messages for your organization, filtering them as needed. Each
    message has the following attributes:

     * **id** - the id of the message (int), filterable as `id`.
     * **broadcast** - the id of the broadcast (int), filterable as `broadcast`.
     * **contact** - the UUID and name of the contact (object), filterable as `contact` with UUID.
     * **urn** - the URN of the sender or receiver, depending on direction (string).
     * **channel** - the UUID of the channel that handled this message (object).
     * **direction** - the direction of the message (one of "incoming" or "outgoing").
     * **type** - the type of the message (one of "inbox", "flow", "ivr").
     * **status** - the status of the message (one of "initializing", "queued", "wired", "sent", "delivered", "handled", "errored", "failed", "resent").
     * **archived** - whether this message is archived (boolean).
     * **text** - the text of the message received (string). Note this is the logical view and the message may have been received as multiple messages.
     * **labels** - any labels set on this message (array of objects), filterable as `label` with label name or UUID.
     * **created_on** - when this message was either received by the channel or created (datetime) (filterable as `before` and `after`).
     * **sent_on** - for outgoing messages, when the channel sent the message (null if not yet sent or an incoming message) (datetime).
     * **delivered_on** - for outgoing messages, when the channel delivered the message (null if not yet sent or an incoming message) (datetime).

    You can also filter by `folder` where folder is one of `inbox`, `flows`, `archived`, `outbox` or `sent`. Note that
    the `contact`, `folder`, `label` and `broadcast` parameters cannot be used together.

    Example:

        GET /api/v2/messages.json?folder=inbox

    Response is the list of messages for that contact, most recently created first:

        {
            "next": "http://example.com/api/v2/messages.json?folder=inbox&cursor=cD0yMDE1LTExLTExKzExJTNBM40NjQlMkIwMCUzRv",
            "previous": null,
            "results": [
            {
                "id": 4105426,
                "broadcast": 2690007,
                "contact": {"uuid": "d33e9ad5-5c35-414c-abd4-e7451c69ff1d", "name": "Bob McFlow"},
                "urn": "twitter:textitin",
                "channel": {"uuid": "9a8b001e-a913-486c-80f4-1356e23f582e", "name": "Nexmo"},
                "direction": "out",
                "type": "inbox",
                "status": "wired",
                "archived": false,
                "text": "How are you?",
                "labels": [{"name": "Important", "uuid": "5a4eb79e-1b1f-4ae3-8700-09384cca385f"}],
                "created_on": "2016-01-06T15:33:00.813162Z",
                "sent_on": "2016-01-06T15:35:03.675716Z",
                "delivered_on": null
            },
            ...
        }
    """
    permission = 'msgs.msg_api'
    model = Msg
    serializer_class = MsgReadSerializer
    pagination_class = CreatedOnCursorPagination

    FOLDER_FILTERS = {'inbox': SystemLabel.TYPE_INBOX,
                      'flows': SystemLabel.TYPE_FLOWS,
                      'archived': SystemLabel.TYPE_ARCHIVED,
                      'outbox': SystemLabel.TYPE_OUTBOX,
                      'sent': SystemLabel.TYPE_SENT}

    def get_queryset(self):
        org = self.request.user.get_org()
        params = self.request.query_params
        folder = params.get('folder')

        # only allowed to filter by one of contact, broadcast, filter or label
        if sum([1 for f in [params.get('contact'), folder, params.get('label'), params.get('broadcast')] if f]) > 1:
            raise InvalidQueryError("Can only specify one of contact, folder, label or broadcast parameters")

        if folder:
            sys_label = self.FOLDER_FILTERS.get(folder.lower())
            if sys_label:
                return SystemLabel.get_queryset(org, sys_label, exclude_test_contacts=False)
            else:
                return self.model.current_messages.filter(pk=-1)
        else:
            return self.model.current_messages.filter(org=org).exclude(visibility=DELETED).exclude(msg_type=None)

    def filter_queryset(self, queryset):
        params = self.request.query_params
        org = self.request.user.get_org()

        # filter by id (optional)
        msg_id = params.get('id')
        if msg_id:
            queryset = queryset.filter(id=msg_id)

        # filter by broadcast (optional)
        broadcast_id = params.get('broadcast')
        if broadcast_id:
            queryset = queryset.filter(broadcast_id=broadcast_id)

        # filter by contact (optional)
        contact_uuid = params.get('contact')
        if contact_uuid:
            contact = Contact.objects.filter(org=org, is_test=False, is_active=True, uuid=contact_uuid).first()
            if contact:
                queryset = queryset.filter(contact=contact)
            else:
                queryset = queryset.filter(pk=-1)
        else:
            # otherwise filter out test contact runs
            test_contacts = Contact.objects.filter(org=org, is_test=True)
            queryset = queryset.exclude(contact__in=test_contacts)

        # filter by label name/uuid (optional)
        label_ref = params.get('label')
        if label_ref:
            label = Label.label_objects.filter(org=org).filter(Q(name=label_ref) | Q(uuid=label_ref)).first()
            if label:
                queryset = queryset.filter(labels=label)
            else:
                queryset = queryset.filter(pk=-1)

        # use prefetch rather than select_related for foreign keys to avoid joins
        queryset = queryset.prefetch_related(
            Prefetch('org', queryset=Org.objects.only('is_anon')),
            Prefetch('contact', queryset=Contact.objects.only('uuid', 'name')),
            Prefetch('contact_urn', queryset=ContactURN.objects.only('urn')),
            Prefetch('channel', queryset=Channel.objects.only('uuid', 'name')),
            Prefetch('labels', queryset=Label.label_objects.only('uuid', 'name')),
        )

        return self.filter_before_after(queryset, 'created_on')

    @classmethod
    def get_read_explorer(cls):
        return {
            'method': "GET",
            'title': "List Messages",
            'url': reverse('api.v2.messages'),
            'slug': 'msg-list',
            'request': "after=2014-01-01T00:00:00.000",
            'fields': [
                {'name': 'broadcast', 'required': False, 'help': "A broadcast ID to filter by, ex: 12345"},
                {'name': 'contact', 'required': False, 'help': "A contact UUID to filter by, ex: 09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"},
                {'name': 'folder', 'required': False, 'help': "A folder name to filter by, one of: inbox, flows, archived, outbox, sent"},
                {'name': 'label', 'required': False, 'help': "A label name or UUID to filter by, ex: Spam"},
                {'name': 'before', 'required': False, 'help': "Only return messages created before this date, ex: 2015-01-28T18:00:00.000"},
                {'name': 'after', 'required': False, 'help': "Only return messages created after this date, ex: 2015-01-28T18:00:00.000"}
            ]
        }


class RunsEndpoint(ListAPIMixin, BaseAPIView):
    """
    This endpoint allows you to fetch flow runs. A run represents a single contact's path through a flow and is created
    each time a contact is started in a flow.

    ## Listing Flow Runs

    By making a ```GET``` request you can list all the flow runs for your organization, filtering them as needed.  Each
    run has the following attributes:

     * **id** - the id of the run (int), filterable as `id`.
     * **flow** - the UUID and name of the flow (object), filterable as `flow` with UUID.
     * **contact** - the UUID and name of the contact (object), filterable as `contact` with UUID.
     * **responded** - whether the contact responded (boolean), filterable as `responded`.
     * **steps** - steps visited by the contact on the flow (array of objects).
     * **created_on** - the datetime when this run was started (datetime).
     * **modified_on** - when this run was last modified (datetime), filterable as `before` and `after`.
     * **exited_on** - the datetime when this run exited or null if it is still active (datetime).
     * **exit_type** - how the run ended (one of "interrupted", "completed", "expired").

    Example:

        GET /api/v2/runs.json?flow=f5901b62-ba76-4003-9c62-72fdacc1b7b7

    Response is the list of runs on the flow, most recently modified first:

        {
            "next": "http://example.com/api/v2/runs.json?cursor=cD0yMDE1LTExLTExKzExJTNBM40NjQlMkIwMCUzRv",
            "previous": null,
            "results": [
            {
                "id": 12345678,
                "flow": {"uuid": "f5901b62-ba76-4003-9c62-72fdacc1b7b7", "name": "Specials"},
                "contact": {"uuid": "d33e9ad5-5c35-414c-abd4-e7451c69ff1d", "name": "Bob McFlow"},
                "responded": true,
                "steps": [
                    {
                        "node": "22bd934e-953b-460d-aaf5-42a84ec8f8af",
                        "category": null,
                        "left_on": "2013-08-19T19:11:21.082Z",
                        "text": "Hi from the Thrift Shop! We are having specials this week. What are you interested in?",
                        "value": null,
                        "arrived_on": "2013-08-19T19:11:21.044Z",
                        "type": "actionset"
                    },
                    {
                        "node": "9a31495d-1c4c-41d5-9018-06f93baa5b98",
                        "category": "Foxes",
                        "left_on": null,
                        "text": "I want to buy a fox skin",
                        "value": "fox skin",
                        "arrived_on": "2013-08-19T19:11:21.088Z",
                        "type": "ruleset"
                    }
                ],
                "created_on": "2015-11-11T13:05:57.457742Z",
                "modified_on": "2015-11-11T13:05:57.576056Z",
                "exited_on": "2015-11-11T13:05:57.576056Z",
                "exit_type": "completed"
            },
            ...
        }

    """
    permission = 'flows.flow_api'
    model = FlowRun
    serializer_class = FlowRunReadSerializer
    pagination_class = ModifiedOnCursorPagination

    def filter_queryset(self, queryset):
        params = self.request.query_params
        org = self.request.user.get_org()

        # filter by flow (optional)
        flow_uuid = params.get('flow')
        if flow_uuid:
            flow = Flow.objects.filter(org=org, uuid=flow_uuid).first()
            if flow:
                queryset = queryset.filter(flow=flow)
            else:
                queryset = queryset.filter(pk=-1)

        # filter by id (optional)
        run_id = params.get('id')
        if run_id:
            queryset = queryset.filter(id=run_id)

        # filter by contact (optional)
        contact_uuid = params.get('contact')
        if contact_uuid:
            contact = Contact.objects.filter(org=org, is_test=False, is_active=True, uuid=contact_uuid).first()
            if contact:
                queryset = queryset.filter(contact=contact)
            else:
                queryset = queryset.filter(pk=-1)
        else:
            # otherwise filter out test contact runs
            test_contacts = Contact.objects.filter(org=org, is_test=True)
            queryset = queryset.exclude(contact__in=test_contacts)

        # limit to responded runs (optional)
        if str_to_bool(params.get('responded')):
            queryset = queryset.filter(responded=True)

        # use prefetch rather than select_related for foreign keys to avoid joins
        queryset = queryset.prefetch_related(
            Prefetch('flow', queryset=Flow.objects.only('uuid', 'name')),
            Prefetch('contact', queryset=Contact.objects.only('uuid', 'name')),
            Prefetch('steps', queryset=FlowStep.objects.order_by('arrived_on')),
            Prefetch('steps__messages', queryset=Msg.all_messages.only('text')),
        )

        return self.filter_before_after(queryset, 'modified_on')

    @classmethod
    def get_read_explorer(cls):
        return {
            'method': "GET",
            'title': "List Flow Runs",
            'url': reverse('api.v2.runs'),
            'slug': 'run-list',
            'request': "after=2014-01-01T00:00:00.000",
            'fields': [
                {'name': 'flow', 'required': False, 'help': "A flow UUID to filter by, ex: f5901b62-ba76-4003-9c62-72fdacc1b7b7"},
                {'name': 'contact', 'required': False, 'help': "A contact UUID to filter by, ex: 09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"},
                {'name': 'responded', 'required': False, 'help': "Whether to only return runs with contact responses"},
                {'name': 'before', 'required': False, 'help': "Only return runs modified before this date, ex: 2015-01-28T18:00:00.000"},
                {'name': 'after', 'required': False, 'help': "Only return runs modified after this date, ex: 2015-01-28T18:00:00.000"}
            ]
        }
