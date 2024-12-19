from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from temba.utils.views.mixins import StaffOnlyMixin

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(StaffOnlyMixin, ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        tps = forms.IntegerField(help_text=_("TPS."), min_value=1, max_value=1000)

    form_class = Form
    readonly_servicing = False

    def form_valid(self, form):
        from .type import TestType

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            None,
            TestType.code,
            "Load Tester",
            config={"send_delay_ms": 10, "error_percent": 5},
            tps=form.cleaned_data["tps"],
        )

        return super().form_valid(form)
