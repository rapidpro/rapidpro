from smartmin.views import SmartCRUDL, SmartListView, SmartReadView, SmartView

from django.http import JsonResponse
from django.views.generic import View

from temba.orgs.views import OrgObjPermsMixin, OrgPermsMixin

from .models import APIToken, Resthook, WebHookResult


class RefreshAPITokenView(OrgPermsMixin, SmartView, View):
    """
    Simple view that refreshes the API token for the user/org when POSTed to
    """

    permission = "api.apitoken_refresh"

    def post(self, request, *args, **kwargs):
        token = APIToken.get_or_create(request.user.get_org(), request.user, refresh=True)
        return JsonResponse(dict(token=token.key))


class WebHookResultCRUDL(SmartCRUDL):
    model = WebHookResult
    actions = ("list", "read")

    class Read(OrgObjPermsMixin, SmartReadView):
        fields = ("url", "status_code", "request_time", "created_on")

    class List(OrgPermsMixin, SmartListView):
        fields = ("url", "status_code", "request_time", "created_on")

        def get_queryset(self):
            return WebHookResult.objects.filter(org=self.request.user.get_org()).order_by("-created_on")


class ResthookList(OrgPermsMixin, SmartListView):
    model = Resthook
    permission = "api.resthook_list"

    def derive_queryset(self):
        return Resthook.objects.filter(is_active=True, org=self.request.user.get_org()).order_by("slug")
