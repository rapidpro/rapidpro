from smartmin.views import SmartFormView, SmartReadView

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN
from temba.orgs.views import OrgPermsMixin
from temba.utils.fields import ExternalURLField
from temba.templates.models import TemplateTranslation

from ...models import Channel
from ...views import ALL_COUNTRIES, ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        number = forms.CharField(help_text=_("Your enterprise WhatsApp number"))
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES, label=_("Country"), help_text=_("The country this phone number is used in")
        )
        base_url = ExternalURLField(help_text=_("The base URL for your 360 Dialog WhatsApp enterprise installation"))

        api_key = forms.CharField(
            max_length=256, help_text=_("The 360 Dialog API key generated after account registration")
        )

        def clean(self):
            # first check that our phone number looks sane
            number, valid = URN.normalize_number(self.cleaned_data["number"], self.cleaned_data["country"])
            if not valid:
                raise forms.ValidationError(_("Please enter a valid phone number"))
            self.cleaned_data["number"] = number

            return self.cleaned_data

    form_class = Form

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()

        data = form.cleaned_data

        config = {
            Channel.CONFIG_BASE_URL: data["base_url"],
            Channel.CONFIG_AUTH_TOKEN: data["api_key"],
        }

        self.object = Channel.create(
            org,
            user,
            data["country"],
            self.channel_type,
            name="WhatsApp: %s" % data["number"],
            address=data["number"],
            config=config,
            tps=45,
        )

        return super().form_valid(form)

class TemplatesView(OrgPermsMixin, SmartReadView):
    """
    Displays a simple table of all the templates synced on this dialog360 Channel
    """

    model = Channel
    fields = ()
    permission = "channels.channel_read"
    slug_url_kwarg = "uuid"
    template_name = "channels/types/dialog360/templates.html"

    def get_gear_links(self):
        return [dict(title=_("Sync logs"), href=reverse("channels.types.dialog360.sync_logs",args=[self.object.uuid]))]

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(org=self.get_user().get_org())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # include all our templates as well
        context["translations"] = TemplateTranslation.objects.filter(channel=self.object).order_by("template__name")
        return context
