from rest_framework import status
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from django.db.models import Prefetch, Q

from temba.channels.models import Channel
from temba.locations.models import AdminBoundary
from temba.notifications.models import Notification
from temba.templates.models import Template, TemplateTranslation

from ..models import APIPermission, SSLPermission
from ..support import APISessionAuthentication, CreatedOnCursorPagination, ModifiedOnCursorPagination
from ..views import BaseAPIView, ListAPIMixin
from . import serializers


class BaseEndpoint(BaseAPIView):
    """
    Base class of all our internal API endpoints
    """

    authentication_classes = (APISessionAuthentication,)
    permission_classes = (SSLPermission, APIPermission)


# ============================================================
# Endpoints (A-Z)
# ============================================================


class LocationsEndpoint(ListAPIMixin, BaseEndpoint):
    """
    Admin boundaries searchable by name at a specified level.
    """

    LEVELS = {
        "state": AdminBoundary.LEVEL_STATE,
        "district": AdminBoundary.LEVEL_DISTRICT,
        "ward": AdminBoundary.LEVEL_WARD,
    }

    class Pagination(CursorPagination):
        ordering = ("name", "id")
        offset_cutoff = 100000

    model = AdminBoundary
    serializer_class = serializers.LocationReadSerializer
    pagination_class = Pagination

    def derive_queryset(self):
        org = self.request.org
        level = self.LEVELS.get(self.request.query_params.get("level"))
        query = self.request.query_params.get("query")

        if not org.country or not level:
            return AdminBoundary.objects.none()

        qs = AdminBoundary.objects.filter(
            path__startswith=f"{org.country.name} {AdminBoundary.PATH_SEPARATOR}", level=level
        )

        if query:
            qs = qs.filter(Q(path__icontains=query))

        return qs.only("osm_id", "name", "path")


class NotificationsEndpoint(ListAPIMixin, BaseEndpoint):
    model = Notification
    pagination_class = CreatedOnCursorPagination
    serializer_class = serializers.ModelAsJsonSerializer

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(org=self.request.org, user=self.request.user, medium__contains=Notification.MEDIUM_UI)
            .prefetch_related("contact_import", "export", "incident")
        )

    def delete(self, request, *args, **kwargs):
        Notification.mark_seen(self.request.org, self.request.user)

        return Response(status=status.HTTP_204_NO_CONTENT)


class TemplatesEndpoint(ListAPIMixin, BaseEndpoint):
    """
    WhatsApp templates with their translations.
    """

    model = Template
    serializer_class = serializers.TemplateReadSerializer
    pagination_class = ModifiedOnCursorPagination

    def filter_queryset(self, queryset):
        org = self.request.org
        queryset = org.templates.exclude(translations=None).prefetch_related(
            Prefetch("translations", TemplateTranslation.objects.order_by("locale")),
            Prefetch("translations__channel", Channel.objects.only("uuid", "name")),
        )
        return self.filter_before_after(queryset, "modified_on").select_related("base_translation__channel")
