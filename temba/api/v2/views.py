from __future__ import absolute_import, unicode_literals

from django.db.models import Prefetch
from django.db.transaction import non_atomic_requests
from rest_framework import generics, mixins, pagination
from rest_framework.response import Response
from temba.contacts.models import Contact
from temba.flows.models import Flow, FlowRun, FlowStep
from temba.msgs.models import Msg
from temba.utils import str_to_bool, json_date_to_datetime
from .serializers import FlowRunReadSerializer
from ..models import ApiPermission, SSLPermission


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
    This endpoint allows you to list and start flow runs.  TODO...

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

        # filter out test contact runs
        test_contacts = Contact.objects.filter(org=org, is_test=True)
        queryset = queryset.exclude(contact__in=test_contacts)

        # limit to responded runs if specified
        if str_to_bool(params.get('responded')):
            queryset = queryset.filter(responded=True)

        # use prefetch rather than select_related for foreign keys flow/contact to avoid joins
        prefetch_steps = Prefetch('steps', queryset=FlowStep.objects.order_by('arrived_on'))
        prefetch_flow = Prefetch('flow', queryset=Flow.objects.only('uuid'))
        prefetch_contact = Prefetch('contact', queryset=Contact.objects.only('uuid'))
        prefetch_msgs = Prefetch('steps__messages', queryset=Msg.all_messages.only('text'))
        queryset = queryset.prefetch_related(prefetch_flow, prefetch_contact, prefetch_steps, prefetch_msgs)

        return self.filter_before_after(queryset, 'modified_on')
