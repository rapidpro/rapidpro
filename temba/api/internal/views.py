from temba.notifications.models import Notification

from ..models import APIPermission, SSLPermission
from ..support import APISessionAuthentication, CreatedOnCursorPagination
from ..views import BaseAPIView, ListAPIMixin
from .serializers import ModelAsJsonSerializer


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
