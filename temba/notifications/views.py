from smartmin.views import SmartCRUDL, SmartListView

from django.http import JsonResponse

from temba.orgs.views import OrgPermsMixin

from .models import Notification


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
