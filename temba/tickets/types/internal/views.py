from django.utils.translation import ugettext_lazy as _

from ...models import Ticketer
from ...views import BaseConnectView


class ConnectView(BaseConnectView):
    class Form(BaseConnectView.Form):
        pass

    form_class = Form

    def get_form_blurb(self):
        brand = self.request.user.get_org().get_branding()
        return _("This will enable handling tickets internally in %(brand)s.") % {"brand": brand["name"]}

    def form_valid(self, form):
        from .type import InternalType

        self.object = Ticketer.create(
            org=self.org, user=self.request.user, ticketer_type=InternalType.slug, config={}, name=f"Internal",
        )

        return super().form_valid(form)
