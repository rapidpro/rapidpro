from __future__ import absolute_import, unicode_literals

from django.db.models import Prefetch
from django.db.transaction import non_atomic_requests
from rest_framework import generics, mixins, pagination
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from smartmin.views import SmartTemplateView
from temba.contacts.models import Contact
from temba.flows.models import Flow, FlowRun, FlowStep
from temba.msgs.models import Msg
from temba.utils import str_to_bool, json_date_to_datetime
from .serializers import FlowRunReadSerializer
from ..models import ApiPermission, SSLPermission


@api_view(['GET'])
@permission_classes((SSLPermission, IsAuthenticated))
def api(request, format=None):
    """
    This is the **under-development** API v2. Everything in this version of the API is subject to change. We strongly
    recommend that most users stick with the existing [API v1](/api/v1) for now.

    The following endpoints are provided:

     * [/api/v2/runs](/api/v2/runs) - To retrieve the geometries of the administrative boundaries on your account.

    You may wish to use the [API Explorer](/api/v2/explorer) to interactively experiment with the API.
    """
    return Response({
        'runs': reverse('api.v2.runs', request=request),
    })


class ApiExplorerView(SmartTemplateView):
    """
    Explorer view which let's users experiment with endpoints against their own data
    """
    template_name = "api/v2/api_explorer.html"

    def get_context_data(self, **kwargs):
        context = super(ApiExplorerView, self).get_context_data(**kwargs)
        context['endpoints'] = [
            FlowRunEndpoint.get_read_explorer()
        ]
        return context


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

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        if not kwargs.get('format', None):
            # if this is just a request to browse the endpoint docs, don't make a query
            return Response([])
        else:
            return super(ListAPIMixin, self).list(request, *args, **kwargs)

    def get_queryset(self):
        return self.model.objects.all()

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


class FlowRunEndpoint(ListAPIMixin, BaseAPIView):
    """
    This endpoint allows you to fetch flow runs. A run represents a single contact's path through a flow and is created
    each time a contact is started in a flow.

    ## Listing Flow Runs

    By making a ```GET``` request you can list all the flow runs for your organization, filtering them as needed.  Each
    run has the following attributes:

    * **id** - the id of the run (int)
    * **flow** - the UUID of the flow (string) (filterable: ```flow```)
    * **contact** - the UUID of the contact (string) filterable: ```contact```)
    * **responded** - whether the contact responded (boolean)
    * **steps** - steps visited by the contact on the flow (array of dictionaries)
    * **created_on** - the datetime when this run was started (datetime)
    * **modified_on** - the datetime when this run was last modified (datetime) (filterable: ```before``` and ```after```)
    * **exited_on** - the datetime when this run exited or null if it is still active (datetime)
    * **exit_type** - how the run ended (string) (one of 'interrupted', 'completed', 'expired')

    Example:

        GET /api/v2/runs.json?flow=f5901b62-ba76-4003-9c62-72fdacc1b7b7

    Response is the list of runs on the flow, most recently modified first:

        {
            "next": "http://example.com/api/v2/runs.json?cursor=cD0yMDE1LTExLTExKzExJTNBM40NjQlMkIwMCUzRv",
            "previous": null,
            "results": [
            {
                "id": 12345678,
                "flow": "f5901b62-ba76-4003-9c62-72fdacc1b7b7",
                "contact": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
                "responded": true,
                "steps": [
                    {
                        "node": "22bd934e-953b-460d-aaf5-42a84ec8f8af",
                        "left_on": "2013-08-19T19:11:21.082Z",
                        "text": "Hi from the Thrift Shop! We are having specials this week. What are you interested in?",
                        "value": null,
                        "arrived_on": "2013-08-19T19:11:21.044Z",
                        "type": "actionset"
                    },
                    {
                        "node": "9a31495d-1c4c-41d5-9018-06f93baa5b98",
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

        # filter by org or a flow
        flow_uuid = params.get('flow')
        if flow_uuid:
            flow = Flow.objects.filter(org=org, uuid=flow_uuid)
            if flow:
                queryset = queryset.filter(flow=flow)
            else:
                queryset = queryset.filter(pk=-1)
        else:
            queryset = queryset.filter(org=org)

        # filter by contact (optional)
        contact_uuid = params.get('contact')
        if contact_uuid:
            contact = Contact.objects.filter(org=org, is_test=False, is_active=True, uuid=contact_uuid)
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

        # use prefetch rather than select_related for foreign keys flow/contact to avoid joins
        prefetch_steps = Prefetch('steps', queryset=FlowStep.objects.order_by('arrived_on'))
        prefetch_flow = Prefetch('flow', queryset=Flow.objects.only('uuid'))
        prefetch_contact = Prefetch('contact', queryset=Contact.objects.only('uuid'))
        prefetch_msgs = Prefetch('steps__messages', queryset=Msg.all_messages.only('text'))
        queryset = queryset.prefetch_related(prefetch_flow, prefetch_contact, prefetch_steps, prefetch_msgs)

        return self.filter_before_after(queryset, 'modified_on')

    @classmethod
    def get_read_explorer(cls):
        return {
            'method': "GET",
            'title': "List Flow Runs",
            'url': reverse('api.v2.runs'),
            'slug': 'run-list',
            'request': "after=2013-01-01T00:00:00.000",
            'fields': [
                {'name': 'flow', 'required': False, 'help': "A flow UUID to filter by, ex: f5901b62-ba76-4003-9c62-72fdacc1b7b7"},
                {'name': 'contact', 'required': False, 'help': "A contact UUID to filter by, ex: 09d23a05-47fe-11e4-bfe9-b8f6b119e9ab"},
                {'name': 'responded', 'required': False, 'help': "Whether to only return runs with contact responses"},
                {'name': 'before', 'required': False, 'help': "Only return runs modified before this date, ex: 2012-01-28T18:00:00.000"},
                {'name': 'after', 'required': False, 'help': "Only return runs modified after this date, ex: 2012-01-28T18:00:00.000"}
            ]
        }
