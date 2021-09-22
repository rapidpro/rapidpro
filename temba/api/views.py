from smartmin.views import SmartView

from django.http import JsonResponse
from django.views.generic import View

from temba.orgs.views import OrgPermsMixin

from .models import APIToken


class RefreshAPITokenView(OrgPermsMixin, SmartView, View):
    """
    Simple view that refreshes the API token for the user/org when POSTed to
    """

    permission = "api.apitoken_refresh"

    def post(self, request, *args, **kwargs):
        token = APIToken.get_or_create(request.user.get_org(), request.user, refresh=True)
        return JsonResponse(dict(token=token.key))
