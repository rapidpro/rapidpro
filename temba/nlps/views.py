from smartmin.views import SmartTemplateView

from .types.bothub.type import BothubType
from .types.wit.type import WitType


class ClaimNLPProviders(SmartTemplateView):
    template_name = "nlps/nlps_claim.haml"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        org = user.get_org()
        context["brand"] = org.get_branding()

        context["nlps_providers"] = [BothubType, WitType]
        return context
