import phonenumbers
from smartmin.views import SmartFormView

from django import forms
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.apks.models import Apk
from temba.contacts.models import ContactURN
from temba.orgs.models import Org

from ...models import Channel
from ...views import ClaimViewMixin, UpdateTelChannelForm


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        claim_code = forms.CharField(max_length=12, help_text=_("The claim code from your Android phone"))
        phone_number = forms.CharField(max_length=15, help_text=_("The phone number of the phone"))

        def __init__(self, *args, **kwargs):
            self.org = kwargs.pop("org")
            super().__init__(*args, **kwargs)

        def clean_claim_code(self):
            claim_code = self.cleaned_data["claim_code"]
            claim_code = claim_code.replace(" ", "").upper()

            # is there a channel with that claim?
            channel = Channel.objects.filter(claim_code=claim_code, is_active=True).first()

            if not channel:
                raise forms.ValidationError(_("Invalid claim code, please check and try again."))
            else:
                self.cleaned_data["channel"] = channel

            return claim_code

        def clean_phone_number(self):
            number = self.cleaned_data["phone_number"]

            if "channel" in self.cleaned_data:
                channel = self.cleaned_data["channel"]

                # ensure number is valid for the channel's country
                try:
                    normalized = phonenumbers.parse(number, channel.country.code)
                    if not phonenumbers.is_possible_number(normalized):
                        raise forms.ValidationError(_("Invalid phone number, try again."))
                except Exception:  # pragma: no cover
                    raise forms.ValidationError(_("Invalid phone number, try again."))

                number = phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.E164)

                # ensure no other active channel has this number
                if self.org.channels.filter(address=number, is_active=True).exclude(pk=channel.pk).exists():
                    raise forms.ValidationError(
                        _("Another channel has this number. Please remove that channel first.")
                    )

            return number

    fields = ("claim_code", "phone_number")
    form_class = Form
    title = _("Connect Android Channel")
    permission = "channels.channel_claim"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["org"] = self.request.user.get_org()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["relayer_app"] = Apk.objects.filter(apk_type=Apk.TYPE_RELAYER).order_by("-created_on").first()
        return context

    def get_success_url(self):
        return "%s?success" % reverse("public.public_welcome")

    def form_valid(self, form):
        org = self.request.user.get_org()

        self.object = Channel.objects.filter(claim_code=self.form.cleaned_data["claim_code"]).first()

        country = self.object.country
        phone_country = ContactURN.derive_country_from_tel(
            self.form.cleaned_data["phone_number"], str(self.object.country)
        )

        # always prefer the country of the phone number they are entering if we have one
        if phone_country and phone_country != country:  # pragma: needs cover
            self.object.country = phone_country

        self.object.claim(org, self.request.user, self.form.cleaned_data["phone_number"])
        self.object.save()

        # trigger a sync
        self.object.trigger_sync()

        return super().form_valid(form)

    def derive_org(self):
        user = self.request.user
        org = None

        if not user.is_anonymous:
            org = user.get_org()

        org_id = self.request.session.get("org_id", None)
        if org_id:  # pragma: needs cover
            org = Org.objects.get(pk=org_id)

        return org


class UpdateForm(UpdateTelChannelForm):
    class Meta(UpdateTelChannelForm.Meta):
        readonly = []
        helps = {"address": _("Phone number of this device")}
