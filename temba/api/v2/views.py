from __future__ import absolute_import, unicode_literals

from django.db.models import Prefetch
from django.db.transaction import non_atomic_requests
from rest_framework import generics, mixins, pagination, filters
from rest_framework.response import Response
from temba.flows.models import FlowRun, FlowStep, RuleSet
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


class FlowRunEndpoint(ListAPIMixin, BaseAPIView):
    """
    This endpoint allows you to list and start flow runs.  TODO...

    """
    permission = 'flows.flow_api'
    model = FlowRun
    serializer_class = FlowRunReadSerializer
    pagination_class = ModifiedOnCursorPagination

    def get_queryset(self):
        org = self.request.user.get_org()
        queryset = self.model.objects.filter(org=org)

        steps_prefetch = Prefetch('steps', queryset=FlowStep.objects.order_by('arrived_on'))

        rulesets_prefetch = Prefetch('flow__rule_sets',
                                     queryset=RuleSet.objects.exclude(label=None).order_by('pk'),
                                     to_attr='ruleset_prefetch')

        # use prefetch rather than select_related for foreign keys flow/contact to avoid joins
        queryset = queryset.prefetch_related('flow', rulesets_prefetch, steps_prefetch, 'steps__messages', 'contact')

        return queryset
