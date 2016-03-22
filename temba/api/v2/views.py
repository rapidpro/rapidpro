from __future__ import absolute_import, unicode_literals

from django.db.models import Prefetch, Q
from django.db.transaction import non_atomic_requests
from rest_framework import generics, mixins, pagination, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from smartmin.views import SmartTemplateView
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactURN, ContactGroup, ContactField
from temba.flows.models import Flow, FlowRun, FlowStep
from temba.msgs.models import Broadcast, Msg, Label, SystemLabel, DELETED
from temba.orgs.models import Org
from temba.utils import str_to_bool, json_date_to_datetime
from .serializers import BroadcastReadSerializer, ContactReadSerializer, ContactFieldReadSerializer
from .serializers import ContactGroupReadSerializer, FlowRunReadSerializer, LabelReadSerializer, MsgReadSerializer
from ..models import ApiPermission, SSLPermission
from ..support import InvalidQueryError


@api_view(['GET'])
@permission_classes((SSLPermission, IsAuthenticated))
def api(request, format=None):
    """
    This is the **under-development** API v2. Everything in this version of the API is subject to change. We strongly
    recommend that most users stick with the existing [API v1](/api/v1) for now.

    The following endpoints are provided:

     * [/api/v2/broadcasts](/api/v2/broadcasts) - to list message broadcasts
     * [/api/v2/contacts](/api/v2/contacts) - to list contacts
     * [/api/v2/fields](/api/v2/fields) - to list contact fields
     * [/api/v2/groups](/api/v2/groups) - to list contact groups
     * [/api/v2/labels](/api/v2/labels) - to list message labels
     * [/api/v2/messages](/api/v2/messages) - to list messages
     * [/api/v2/org](/api/v2/org) - to view your org
     * [/api/v2/runs](/api/v2/runs) - to list flow runs

    You may wish to use the [API Explorer](/api/v2/explorer) to interactively experiment with the API.
    """
    return Response({
        'broadcasts': reverse('api.v2.broadcasts', request=request),
        'contacts': reverse('api.v2.contacts', request=request),
        'fields': reverse('api.v2.fields', request=request),
        'groups': reverse('api.v2.groups', request=request),
        'labels': reverse('api.v2.labels', request=request),
        'messages': reverse('api.v2.messages', request=request),
        'org': reverse('api.v2.org', request=request),
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
            BroadcastEndpoint.get_read_explorer(),
            ContactsEndpoint.get_read_explorer(),
            FieldsEndpoint.get_read_explorer(),
            GroupsEndpoint.get_read_explorer(),
            LabelsEndpoint.get_read_explorer(),
            MessagesEndpoint.get_read_explorer(),
            OrgEndpoint.get_read_explorer(),
            RunsEndpoint.get_read_explorer()
        ]
        return context


class CreatedOnCursorPagination(pagination.CursorPagination):
    ordering = '-created_on'


class ModifiedOnCursorPagination(pagination.CursorPagination):
    ordering = '-modified_on'


class MsgCursorPagination(pagination.CursorPagination):
    """
    Overridden paginator for Msg endpoint that switches from created_on to modified_on when looking
    at all incoming messages.
    """
    def get_ordering(self, request, queryset, view=None):
        # if this is our incoming folder, order by modified_on
        if request.query_params.get('folder', '').lower() == 'incoming':
            return ['-modified_on']
        else:
            return ['-created_on']


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
    exclusive_params = ()
    required_params = ()

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        self.check_query(self.request.query_params)

        if not kwargs.get('format', None):
            # if this is just a request to browse the endpoint docs, don't make a query
            return Response([])
        else:
            return super(ListAPIMixin, self).list(request, *args, **kwargs)

    def check_query(self, params):
        # check user hasn't provided values for more than one of any exclusive params
        if sum([(1 if params.get(p) else 0) for p in self.exclusive_params]) > 1:
            raise InvalidQueryError("You may only specify one of the %s parameters" % ", ".join(self.exclusive_params))

        # check that any required params are included
        if self.required_params:
            if sum([(1 if params.get(p) else 0) for p in self.required_params]) != 1:
                raise InvalidQueryError("You must specify one of the %s parameters" % ", ".join(self.required_params))

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

class BroadcastEndpoint(ListAPIMixin, BaseAPIView):
    """
    This endpoint allows you to list message broadcasts on your account using the ```GET``` method.

    ## Listing Broadcasts

    Returns the message activity for your organization, listing the most recent messages first.

     * **id** - the id of the broadcast (int), filterable as `id`.
     * **urns** - the URNs that received the broadcast (array of strings)
     * **contacts** - the contacts that received the broadcast (array of objects)
     * **groups** - the groups that received the broadcast (array of objects)
     * **text** - the message text (string)
     * **created_on** - when this broadcast was either created (datetime) (filterable as `before` and `after`).
     * **status** - the status of the broadcast (one of "initializing", "queued", "wired", "sent", "delivered", "errored", "failed", "resent").

    Example:

        GET /api/v2/broadcasts.json

    Response is a list of recent broadcasts:

        {
            "next": null,
            "previous": null,
            "results": [
                {
                    "id": 123456,
                    "urns": ["tel:+250788123123", "tel:+250788123124"],
                    "contacts": [{"uuid": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab", "name": "Joe"}]
                    "groups": [],
                    "text": "hello world",
                    "created_on": "2013-03-02T17:28:12.123Z",
                    "status": "queued"
                },
                ...
    """
    permission = 'msgs.broadcast_api'
    model = Broadcast
    serializer_class = BroadcastReadSerializer
    pagination_class = CreatedOnCursorPagination

    def filter_queryset(self, queryset):
        params = self.request.query_params
        org = self.request.user.get_org()

        queryset = queryset.filter(is_active=True)

        # filter by id (optional)
        msg_id = params.get('id')
        if msg_id:
            queryset = queryset.filter(id=msg_id)

        queryset = queryset.prefetch_related(
            Prefetch('org', queryset=Org.objects.only('is_anon')),
            Prefetch('contacts', queryset=Contact.objects.only('uuid', 'name')),
            Prefetch('groups', queryset=ContactGroup.user_groups.only('uuid', 'name')),
        )

        if not org.is_anon:
            queryset = queryset.prefetch_related(Prefetch('urns', queryset=ContactURN.objects.only('urn')))

        return self.filter_before_after(queryset, 'created_on')

    @classmethod
    def get_read_explorer(cls):
        return {
            'method': "GET",
            'title': "List broadcasts",
            'url': reverse('api.v2.broadcasts'),
            'slug': 'broadcast-list',
            'request': "",
            'fields': [
                {'name': 'id', 'required': False, 'help': "A broadcast ID to filter by, ex: 123456"},
                {'name': 'before', 'required': False, 'help': "Only return broadcasts created before this date, ex: 2015-01-28T18:00:00.000"},
                {'name': 'after', 'required': False, 'help': "Only return broadcasts created after this date, ex: 2015-01-28T18:00:00.000"}
            ]
        }


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
    throttle_scope = 'v2.contacts'

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
            group = ContactGroup.user_groups.filter(org=org).filter(Q(uuid=group_ref) | Q(name=group_ref)).first()
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
                {'name': "deleted", 'required': False, 'help': "Whether to return only deleted contacts. ex: false"},
                {'name': 'before', 'required': False, 'help': "Only return contacts modified before this date, ex: 2015-01-28T18:00:00.000"},
                {'name': 'after', 'required': False, 'help': "Only return contacts modified after this date, ex: 2015-01-28T18:00:00.000"}
            ]
        }


class FieldsEndpoint(ListAPIMixin, BaseAPIView):
    """
    ## Listing Fields

    A **GET** returns the list of custom contact fields for your organization, in the order of last created.

    * **key** - the unique key of this field (string), filterable as `key`
    * **label** - the display label of this field (string)
    * **value_type** - the data type of values associated with this field (string)

    Example:

        GET /api/v2/fields.json

    Response containing the fields for your organization:

         {
            "next": null,
            "previous": null,
            "results": [
                {
                    "key": "nick_name",
                    "label": "Nick name",
                    "value_type": "text"
                },
                ...
            ]
        }
    """
    permission = 'contacts.contactfield_api'
    model = ContactField
    serializer_class = ContactFieldReadSerializer
    pagination_class = CreatedOnCursorPagination

    def filter_queryset(self, queryset):
        params = self.request.query_params

        # filter by key (optional)
        key = params.get('key')
        if key:
            queryset = queryset.filter(key=key)

        return queryset.filter(is_active=True)

    @classmethod
    def get_read_explorer(cls):
        return {
            'method': "GET",
            'title': "List Fields",
            'url': reverse('api.v2.fields'),
            'slug': 'field-list',
            'request': "key=nick_name",
            'fields': [
                {'name': "key", 'required': False, 'help': "A field key to filter by. ex: nick_name"}
            ]
        }


class GroupsEndpoint(ListAPIMixin, BaseAPIView):
    """
    ## Listing Groups

    A **GET** returns the list of contact groups for your organization, in the order of last created.

    * **uuid** - the UUID of the group (string), filterable as `uuid`
    * **name** - the name of the group (string)
    * **count** - the number of contacts in the group (int)

    Example:

        GET /api/v2/groups.json

    Response containing the groups for your organization:

        {
            "next": null,
            "previous": null,
            "results": [
                {
                    "uuid": "5f05311e-8f81-4a67-a5b5-1501b6d6496a",
                    "name": "Reporters",
                    "count": 315
                },
                ...
            ]
        }
    """
    permission = 'contacts.contactgroup_api'
    model = ContactGroup
    model_manager = 'user_groups'
    serializer_class = ContactGroupReadSerializer
    pagination_class = CreatedOnCursorPagination

    def filter_queryset(self, queryset):
        params = self.request.query_params

        # filter by UUID (optional)
        uuid = params.get('uuid')
        if uuid:
            queryset = queryset.filter(uuid=uuid)

        return queryset.filter(is_active=True)

    @classmethod
    def get_read_explorer(cls):
        return {
            'method': "GET",
            'title': "List Groups",
            'url': reverse('api.v2.groups'),
            'slug': 'group-list',
            'request': "",
            'fields': [
                {'name': "uuid", 'required': False, 'help': "A group UUID filter by. ex: 5f05311e-8f81-4a67-a5b5-1501b6d6496a"}
            ]
        }


class LabelsEndpoint(ListAPIMixin, BaseAPIView):
    """
    ## Listing Labels

    A **GET** returns the list of message labels for your organization, in the order of last created.

    * **uuid** - the UUID of the label (string), filterable as `uuid`
    * **name** - the name of the label (string)
    * **count** - the number of messages with this label (int)

    Example:

        GET /api/v2/labels.json

    Response containing the labels for your organization:

        {
            "next": null,
            "previous": null,
            "results": [
                {
                    "uuid": "5f05311e-8f81-4a67-a5b5-1501b6d6496a",
                    "name": "Screened",
                    "count": 315
                },
                ...
            ]
        }
    """
    permission = 'contacts.label_api'
    model = Label
    model_manager = 'label_objects'
    serializer_class = LabelReadSerializer
    pagination_class = CreatedOnCursorPagination

    def filter_queryset(self, queryset):
        params = self.request.query_params

        # filter by UUID (optional)
        uuid = params.get('uuid')
        if uuid:
            queryset = queryset.filter(uuid=uuid)

        return queryset.filter(is_active=True)

    @classmethod
    def get_read_explorer(cls):
        return {
            'method': "GET",
            'title': "List Labels",
            'url': reverse('api.v2.labels'),
            'slug': 'label-list',
            'request': "",
            'fields': [
                {'name': "uuid", 'required': False, 'help': "A label UUID filter by. ex: 5f05311e-8f81-4a67-a5b5-1501b6d6496a"}
            ]
        }


class MessagesEndpoint(ListAPIMixin, BaseAPIView):
    """
    This endpoint allows you to fetch messages.

    ## Listing Messages

    By making a ```GET``` request you can list the messages for your organization, filtering them as needed. Each
    message has the following attributes:

     * **id** - the id of the message (int), filterable as `id`.
     * **broadcast** - the id of the broadcast (int), filterable as `broadcast`.
     * **contact** - the UUID and name of the contact (object), filterable as `contact` with UUID.
     * **urn** - the URN of the sender or receiver, depending on direction (string).
     * **channel** - the UUID of the channel that handled this message (object).
     * **direction** - the direction of the message (one of "incoming" or "outgoing").
     * **type** - the type of the message (one of "inbox", "flow", "ivr").
     * **status** - the status of the message (one of "initializing", "queued", "wired", "sent", "delivered", "handled", "errored", "failed", "resent").
     * **visibility** - the visibility of the message (one of "visible", "archived" or "deleted")
     * **text** - the text of the message received (string). Note this is the logical view and the message may have been received as multiple physical messages.
     * **labels** - any labels set on this message (array of objects), filterable as `label` with label name or UUID.
     * **created_on** - when this message was either received by the channel or created (datetime) (filterable as `before` and `after`).
     * **sent_on** - for outgoing messages, when the channel sent the message (null if not yet sent or an incoming message) (datetime).

    You can also filter by `folder` where folder is one of `inbox`, `flows`, `archived`, `outbox`, `incoming` or `sent`.
    Note that you cannot filter by more than one of `contact`, `folder`, `label` or `broadcast` at the same time.

    The sort order for all folders save for `incoming` is the message creation date. For the `incoming` folder (which
    includes all incoming messages, regardless of visibility or type) messages are sorted by last modified date. This
    allows clients to poll for updates to message labels and visibility changes.

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
                "visibility": "visible",
                "text": "How are you?",
                "labels": [{"name": "Important", "uuid": "5a4eb79e-1b1f-4ae3-8700-09384cca385f"}],
                "created_on": "2016-01-06T15:33:00.813162Z",
                "sent_on": "2016-01-06T15:35:03.675716Z",
            },
            ...
        }
    """
    permission = 'msgs.msg_api'
    model = Msg
    serializer_class = MsgReadSerializer
    pagination_class = MsgCursorPagination
    exclusive_params = ('contact', 'folder', 'label', 'broadcast')
    required_params = ('contact', 'folder', 'label', 'broadcast', 'id')
    throttle_scope = 'v2.messages'

    FOLDER_FILTERS = {'inbox': SystemLabel.TYPE_INBOX,
                      'flows': SystemLabel.TYPE_FLOWS,
                      'archived': SystemLabel.TYPE_ARCHIVED,
                      'outbox': SystemLabel.TYPE_OUTBOX,
                      'sent': SystemLabel.TYPE_SENT}

    def get_queryset(self):
        org = self.request.user.get_org()
        folder = self.request.query_params.get('folder')

        if folder:
            sys_label = self.FOLDER_FILTERS.get(folder.lower())
            if sys_label:
                return SystemLabel.get_queryset(org, sys_label, exclude_test_contacts=False)
            elif folder == 'incoming':
                return self.model.all_messages.filter(org=org, direction='I')
            else:
                return self.model.all_messages.filter(pk=-1)
        else:
            return self.model.all_messages.filter(org=org).exclude(visibility=DELETED).exclude(msg_type=None)

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
            test_contact_ids = list(Contact.objects.filter(org=org, is_test=True).values_list('pk', flat=True))
            queryset = queryset.exclude(contact__pk__in=test_contact_ids)

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

        # incoming folder gets sorted by 'modified_on'
        if self.request.query_params.get('folder', '').lower() == 'incoming':
            return self.filter_before_after(queryset, 'modified_on')

        # everything else by 'created_on'
        else:
            return self.filter_before_after(queryset, 'created_on')

    @classmethod
    def get_read_explorer(cls):
        return {
            'method': "GET",
            'title': "List Messages",
            'url': reverse('api.v2.messages'),
            'slug': 'msg-list',
            'request': "folder=incoming&after=2014-01-01T00:00:00.000",
            'fields': [
                {'name': 'id', 'required': False, 'help': "A message ID to filter by, ex: 123456"},
                {'name': 'broadcast', 'required': False, 'help': "A broadcast ID to filter by, ex: 12345"},
                {'name': 'contact', 'required': False, 'help': "A contact UUID to filter by, ex: 09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"},
                {'name': 'folder', 'required': False, 'help': "A folder name to filter by, one of: inbox, flows, archived, outbox, sent, incoming"},
                {'name': 'label', 'required': False, 'help': "A label name or UUID to filter by, ex: Spam"},
                {'name': 'before', 'required': False, 'help': "Only return messages created before this date, ex: 2015-01-28T18:00:00.000"},
                {'name': 'after', 'required': False, 'help': "Only return messages created after this date, ex: 2015-01-28T18:00:00.000"}
            ]
        }


class OrgEndpoint(BaseAPIView):
    """
    ## Viewing Current Organization

    A **GET** returns the details of your organization. There are no parameters.

    Example:

        GET /api/v2/org.json

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

        data = {
            'name': org.name,
            'country': org.get_country_code(),
            'languages': [l.iso_code for l in org.languages.order_by('iso_code')],
            'primary_language': org.primary_language.iso_code if org.primary_language else None,
            'timezone': org.timezone,
            'date_style': ('day_first' if org.get_dayfirst() else 'month_first'),
            'anon': org.is_anon
        }

        return Response(data, status=status.HTTP_200_OK)

    @classmethod
    def get_read_explorer(cls):
        return {
            'method': "GET",
            'title': "View Current Org",
            'url': reverse('api.v2.org'),
            'slug': 'org-read',
            'request': ""
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

    Note that you cannot filter by `flow` and `contact` at the same time.

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
    exclusive_params = ('contact', 'flow')
    throttle_scope = 'v2.runs'

    def filter_queryset(self, queryset):
        params = self.request.query_params
        org = self.request.user.get_org()

        # filter by flow (optional)
        flow_uuid = params.get('flow')
        if flow_uuid:
            flow = Flow.objects.filter(org=org, uuid=flow_uuid, is_active=True).first()
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
            test_contact_ids = list(Contact.objects.filter(org=org, is_test=True).values_list('pk', flat=True))
            queryset = queryset.exclude(contact__pk__in=test_contact_ids)

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
                {'name': 'id', 'required': False, 'help': "A run ID to filter by, ex: 123456"},
                {'name': 'flow', 'required': False, 'help': "A flow UUID to filter by, ex: f5901b62-ba76-4003-9c62-72fdacc1b7b7"},
                {'name': 'contact', 'required': False, 'help': "A contact UUID to filter by, ex: 09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"},
                {'name': 'responded', 'required': False, 'help': "Whether to only return runs with contact responses"},
                {'name': 'before', 'required': False, 'help': "Only return runs modified before this date, ex: 2015-01-28T18:00:00.000"},
                {'name': 'after', 'required': False, 'help': "Only return runs modified after this date, ex: 2015-01-28T18:00:00.000"}
            ]
        }
