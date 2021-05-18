from smartmin.views import SmartFormView

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN
from temba.utils.fields import ExternalURLField, SelectMultipleWidget, SelectWidget

from ...models import Channel
from ...views import ALL_COUNTRIES, ClaimViewMixin, UpdateTelChannelForm


class ClaimView(ClaimViewMixin, SmartFormView):
    class ClaimForm(ClaimViewMixin.Form):
        scheme = forms.ChoiceField(
            choices=URN.SCHEME_CHOICES, label=_("URN Type"), help_text=_("The type of URNs handled by this channel"),
        )

        number = forms.CharField(
            max_length=14,
            min_length=1,
            label=_("Number"),
            required=False,
            help_text=_("The phone number or that this channel will send from"),
        )

        handle = forms.CharField(
            max_length=32,
            min_length=1,
            label=_("Handle"),
            required=False,
            help_text=_("The Twitter handle that this channel will send from"),
        )

        address = forms.CharField(
            max_length=64,
            min_length=1,
            label=_("Address"),
            required=False,
            help_text=_("The external address that this channel will send from"),
        )

        country = forms.ChoiceField(
            choices=ALL_COUNTRIES,
            label=_("Country"),
            required=False,
            widget=SelectWidget(attrs={"searchable": True}),
            help_text=_("The country this phone number is used in"),
        )

        method = forms.ChoiceField(
            choices=(("POST", "HTTP POST"), ("GET", "HTTP GET"), ("PUT", "HTTP PUT")),
            help_text=_("What HTTP method to use when calling the URL"),
        )

        encoding = forms.ChoiceField(
            choices=Channel.ENCODING_CHOICES,
            label=_("Encoding"),
            help_text=_("What encoding to use for outgoing messages"),
        )

        content_type = forms.ChoiceField(
            choices=Channel.CONTENT_TYPE_CHOICES, help_text=_("The content type used when sending the request")
        )

        max_length = forms.IntegerField(
            initial=160,
            validators=[MaxValueValidator(640), MinValueValidator(60)],
            help_text=_(
                "The maximum length of any single message on this channel. " "(longer messages will be split)"
            ),
        )

        send_authorization = forms.CharField(
            max_length=2048,
            label=_("Authorization Header Value"),
            required=False,
            help_text=_("The Authorization header value added when calling the URL (if any)"),
        )

        url = ExternalURLField(
            max_length=1024,
            label=_("Send URL"),
            help_text=_("The URL we will call when sending messages, with variable substitutions"),
        )

        body = forms.CharField(
            max_length=2048,
            label=_("Request Body"),
            required=False,
            widget=forms.Textarea,
            help_text=_("The request body if any, with variable substitutions (only used for PUT or POST)"),
        )

        mt_response_check = forms.CharField(
            max_length=2048,
            label=_("MT Response check"),
            required=False,
            widget=forms.Textarea,
            help_text=_("The content that must be in the response to consider the request successful"),
        )

    class SendClaimForm(ClaimViewMixin.Form):
        url = ExternalURLField(
            max_length=1024,
            label=_("Send URL"),
            help_text=_("The URL we will POST to when sending messages, with variable substitutions"),
        )

        method = forms.ChoiceField(
            choices=(("POST", "HTTP POST"), ("GET", "HTTP GET"), ("PUT", "HTTP PUT")),
            help_text=_("What HTTP method to use when calling the URL"),
        )

        encoding = forms.ChoiceField(
            choices=Channel.ENCODING_CHOICES,
            label=_("Encoding"),
            help_text=_("What encoding to use for outgoing messages"),
        )

        content_type = forms.ChoiceField(
            choices=Channel.CONTENT_TYPE_CHOICES, help_text=_("The content type used when sending the request")
        )

        max_length = forms.IntegerField(
            initial=160,
            validators=[MaxValueValidator(640), MinValueValidator(60)],
            help_text=_(
                "The maximum length of any single message on this channel. " "(longer messages will be split)"
            ),
        )

        send_authorization = forms.CharField(
            max_length=2048,
            label=_("Authorization Header Value"),
            required=False,
            help_text=_("The Authorization header value added when calling the URL (if any)"),
        )

        body = forms.CharField(
            max_length=2048,
            label=_("Request Body"),
            required=False,
            widget=forms.Textarea,
            help_text=_("The request body if any, with variable substitutions (only used for PUT or POST)"),
        )

        mt_response_check = forms.CharField(
            max_length=2048,
            label=_("MT Response check"),
            required=False,
            widget=forms.Textarea,
            help_text=_("The content that must be in the response to consider the request successful"),
        )

    title = "Connect External Service"
    permission = "channels.channel_claim"
    success_url = "uuid@channels.channel_configuration"

    def derive_initial(self):
        from .type import ExternalType

        return {"body": ExternalType.CONFIG_DEFAULT_SEND_BODY}

    def get_form_class(self):
        if self.request.GET.get("role", None) == "S":  # pragma: needs cover
            return ClaimView.SendClaimForm
        else:
            return ClaimView.ClaimForm

    def form_valid(self, form):
        from .type import ExternalType

        org = self.request.user.get_org()
        data = form.cleaned_data

        if self.request.GET.get("role", None) == "S":  # pragma: needs cover
            # get our existing channel
            receive = org.get_receive_channel(URN.TEL_SCHEME)
            role = Channel.ROLE_SEND
            scheme = URN.TEL_SCHEME
            address = receive.address
            country = receive.country
        else:
            role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE
            scheme = data["scheme"]
            if scheme == URN.TEL_SCHEME:
                address = data["number"]
                country = data["country"]
            elif scheme == URN.TWITTER_SCHEME:  # pragma: needs cover
                address = data["handle"]
                country = None
            else:  # pragma: needs cover
                address = data["address"]
                country = None

        # see if there is a parent channel we are adding a delegate for
        channel = self.request.GET.get("channel", None)
        if channel:  # pragma: needs cover
            # make sure they own it
            channel = self.request.user.get_org().channels.filter(pk=channel).first()

        config = {
            Channel.CONFIG_SEND_URL: data["url"],
            ExternalType.CONFIG_SEND_METHOD: data["method"],
            ExternalType.CONFIG_CONTENT_TYPE: data["content_type"],
            ExternalType.CONFIG_MAX_LENGTH: data["max_length"],
            Channel.CONFIG_ENCODING: data.get("encoding", Channel.ENCODING_DEFAULT),
        }

        if "send_authorization" in data:
            config[ExternalType.CONFIG_SEND_AUTHORIZATION] = data["send_authorization"]

        if "body" in data:
            config[ExternalType.CONFIG_SEND_BODY] = data["body"]

        if "mt_response_check" in data:
            config[ExternalType.CONFIG_MT_RESPONSE_CHECK] = data["mt_response_check"]

        self.object = Channel.add_config_external_channel(
            org, self.request.user, country, address, self.channel_type, config, role, [scheme], parent=channel
        )

        return super().form_valid(form)


class UpdateForm(UpdateTelChannelForm):
    role = forms.MultipleChoiceField(
        choices=((Channel.ROLE_RECEIVE, _("Receive")), (Channel.ROLE_SEND, _("Send"))),
        widget=SelectMultipleWidget(attrs={"widget_only": True}),
        label=_("Channel Role"),
        help_text=_("The roles this channel can fulfill"),
    )

    def clean_role(self):
        return "".join(self.cleaned_data.get("role", []))

    class Meta(UpdateTelChannelForm.Meta):
        fields = "name", "alert_email", "role"
        readonly = []
