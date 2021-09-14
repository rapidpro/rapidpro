from smartmin.views import SmartCRUDL, SmartListView

from django.http import JsonResponse

from temba.orgs.views import OrgPermsMixin

from .models import Notification


class NotificationTargetMixin:
    """
    Mixin for views which can be targets of notifications to help them clear unseen notifications
    """

    notification_type = None
    notification_scope = ""

    def get_notification_scope(self) -> tuple:  # pragma: no cover
        return self.notification_type, self.notification_scope

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)

        notification_type, scope = self.get_notification_scope()
        if request.org and notification_type and request.user.is_authenticated:
            Notification.mark_seen(request.org, notification_type, scope=scope, user=request.user)

        return response


class NotificationCRUDL(SmartCRUDL):
    model = Notification
    actions = ("list",)

    class List(OrgPermsMixin, SmartListView):
        default_order = "-id"
        select_related = ("org",)
        prefetch_related = ("channel", "contact_import", "contact_export", "message_export", "results_export")

        def get_queryset(self, **kwargs):
            return (
                super()
                .get_queryset(**kwargs)
                .filter(org=self.org, user=self.request.user)
                .prefetch_related(*self.prefetch_related)
            )

        def render_to_response(self, context, **response_kwargs):
            return JsonResponse(
                {"results": [n.as_json() for n in context["object_list"]]}, json_dumps_params={"indent": 2}
            )
