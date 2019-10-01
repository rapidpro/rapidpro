from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.channels.views import ClaimViewMixin


class BothubView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        test_field = forms.CharField(label=_("Test Field"), help_text=_("Test Field"))

    form_class = Form

    def form_valid(self, form):
        # To Do

        return super().form_valid(form)
