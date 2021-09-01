from smartmin.views import SmartCRUDL, SmartListView

from django.http import JsonResponse

from temba.orgs.views import OrgPermsMixin

from .models import Log, Notification


class LogCRUDL(SmartCRUDL):
    model = Log
    actions = ("list",)

    class List(OrgPermsMixin, SmartListView):
        default_order = "-created_on"
        select_related = ("org", "created_by")
        prefetch_related = (
            "alert__channel",
            "contact_import",
            "contact_export",
            "message_export",
            "results_export",
            "ticket",
        )

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).filter(org=self.org).prefetch_related(*self.prefetch_related)

        def render_to_response(self, context, **response_kwargs):
            return JsonResponse(
                {"results": [l.as_json() for l in context["object_list"]]}, json_dumps_params={"indent": 2}
            )


class NotificationCRUDL(SmartCRUDL):
    model = Notification
    actions = ("list",)

    class List(OrgPermsMixin, SmartListView):
        default_order = "-id"
        select_related = ("log", "log__org", "log__created_by")
        prefetch_related = tuple(f"log__{pf}" for pf in LogCRUDL.List.prefetch_related)

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
