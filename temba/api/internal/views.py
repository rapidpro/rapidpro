from rest_framework import status
from rest_framework.response import Response

from django.db.models import Prefetch

from temba.channels.models import Channel
from temba.notifications.models import Notification
from temba.templates.models import Template, TemplateTranslation

from ..models import APIPermission, SSLPermission
from ..support import APISessionAuthentication, CreatedOnCursorPagination, ModifiedOnCursorPagination
from ..views import BaseAPIView, ListAPIMixin
from .serializers import ModelAsJsonSerializer, TemplateReadSerializer


class BaseEndpoint(BaseAPIView):
    """
    Base class of all our internal API endpoints
    """

    authentication_classes = (APISessionAuthentication,)
    permission_classes = (SSLPermission, APIPermission)


# ============================================================
# Endpoints (A-Z)
# ============================================================


class NotificationsEndpoint(ListAPIMixin, BaseEndpoint):
    model = Notification
    pagination_class = CreatedOnCursorPagination
    serializer_class = ModelAsJsonSerializer

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(org=self.request.org, user=self.request.user, medium__contains=Notification.MEDIUM_UI)
            .prefetch_related(
                "contact_import", "contact_export", "message_export", "results_export", "export", "incident"
            )
        )

    def delete(self, request, *args, **kwargs):
        Notification.mark_seen(self.request.org, self.request.user)

        return Response(status=status.HTTP_204_NO_CONTENT)


class TemplatesEndpoint(ListAPIMixin, BaseEndpoint):
    """
    WhatsApp templates with their translations.
    """

    model = Template
    serializer_class = TemplateReadSerializer
    pagination_class = ModifiedOnCursorPagination

    def filter_queryset(self, queryset):
        org = self.request.org
        queryset = org.templates.exclude(translations=None).prefetch_related(
            Prefetch("translations", TemplateTranslation.objects.filter(is_active=True).order_by("locale")),
            Prefetch("translations__channel", Channel.objects.only("uuid", "name")),
        )
        return self.filter_before_after(queryset, "modified_on")
