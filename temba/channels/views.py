import base64
import hashlib
import hmac
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta

import nexmo
import phonenumbers
import pytz
import requests
import twilio.base.exceptions
from smartmin.views import (
    SmartCRUDL,
    SmartFormView,
    SmartListView,
    SmartModelActionView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
)
from twilio.base.exceptions import TwilioRestException

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Sum
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from temba.contacts.models import URN
from temba.ivr.models import Call
from temba.msgs.models import Msg
from temba.orgs.views import DependencyDeleteModal, MenuMixin, ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils import analytics, countries, json
from temba.utils.fields import SelectWidget
from temba.utils.models import patch_queryset_count
from temba.utils.views import ComponentFormMixin, ContentMenuMixin, SpaMixin

from .models import Alert, Channel, ChannelCount, ChannelEvent, ChannelLog, SyncEvent, UnsupportedAndroidChannelError

logger = logging.getLogger(__name__)

ALL_COUNTRIES = countries.choices()


def get_channel_read_url(channel):
    return reverse("channels.channel_read", args=[channel.uuid])


def get_commands(channel, commands, sync_event=None):
    """
    Generates sync commands for all queued messages on the given channel
    """
    msgs = Msg.objects.filter(
        status__in=(Msg.STATUS_PENDING, Msg.STATUS_QUEUED, Msg.STATUS_WIRED),
        channel=channel,
        direction=Msg.DIRECTION_OUT,
    )

    if sync_event:
        pending_msgs = sync_event.get_pending_messages()
        retry_msgs = sync_event.get_retry_messages()
        msgs = msgs.exclude(id__in=pending_msgs).exclude(id__in=retry_msgs)

    commands += Msg.get_sync_commands(msgs=msgs)

    # TODO: add in other commands for the channel
    # We need a queueable model similar to messages for sending arbitrary commands to the client

    return commands


@csrf_exempt
def sync(request, channel_id):
    start = time.time()

    if request.method != "POST":
        return HttpResponse(status=500, content="POST Required")

    commands = []
    channel = Channel.objects.filter(id=channel_id, is_active=True).first()
    if not channel:
        return JsonResponse(dict(cmds=[dict(cmd="rel", relayer_id=channel_id)]))

    request_time = request.GET.get("ts", "")
    request_signature = force_bytes(request.GET.get("signature", ""))

    if not channel.secret:
        return JsonResponse({"error_id": 4, "error": "Can't sync unclaimed channel", "cmds": []}, status=401)

    # check that the request isn't too old (15 mins)
    now = time.time()
    if abs(now - int(request_time)) > 60 * 15:
        return JsonResponse({"error_id": 3, "error": "Old Request", "cmds": []}, status=401)

    # sign the request
    signature = hmac.new(
        key=force_bytes(str(channel.secret + request_time)), msg=force_bytes(request.body), digestmod=hashlib.sha256
    ).digest()

    # base64 and url sanitize
    signature = base64.urlsafe_b64encode(signature).strip()

    if request_signature != signature:
        return JsonResponse(
            {"error_id": 1, "error": "Invalid signature: '%(request)s'" % {"request": request_signature}, "cmds": []},
            status=401,
        )

    # update our last seen on our channel if we haven't seen this channel in a bit
    if not channel.last_seen or timezone.now() - channel.last_seen > timedelta(minutes=5):
        channel.last_seen = timezone.now()
        channel.save(update_fields=["last_seen"])

    sync_event = None

    # Take the update from the client
    cmds = []
    if request.body:
        body_parsed = json.loads(request.body)

        # all valid requests have to begin with a FCM command
        if "cmds" not in body_parsed or len(body_parsed["cmds"]) < 1 or body_parsed["cmds"][0]["cmd"] != "fcm":
            return JsonResponse({"error_id": 4, "error": "Missing FCM command", "cmds": []}, status=401)

        cmds = body_parsed["cmds"]

    if not channel.org and channel.uuid == cmds[0].get("uuid"):
        # Unclaimed channel with same UUID resend the registration commmands
        cmd = dict(
            cmd="reg", relayer_claim_code=channel.claim_code, relayer_secret=channel.secret, relayer_id=channel.id
        )
        return JsonResponse(dict(cmds=[cmd]))
    elif not channel.org:
        return JsonResponse({"error_id": 4, "error": "Can't sync unclaimed channel", "cmds": []}, status=401)

    unique_calls = set()

    for cmd in cmds:
        handled = False
        extra = None

        if "cmd" in cmd:
            keyword = cmd["cmd"]

            # catchall for commands that deal with a single message
            if "msg_id" in cmd:

                # make sure the negative ids are converted to long
                msg_id = cmd["msg_id"]
                if msg_id < 0:
                    msg_id = 4294967296 + msg_id

                msg = Msg.objects.filter(id=msg_id, org=channel.org).first()
                if msg:
                    if msg.direction == Msg.DIRECTION_OUT:
                        handled = msg.update(cmd)
                    else:
                        handled = True

            # creating a new message
            elif keyword == "mo_sms":
                date = datetime.fromtimestamp(int(cmd["ts"]) // 1000).replace(tzinfo=pytz.utc)

                # it is possible to receive spam SMS messages from no number on some carriers
                tel = cmd["phone"] if cmd["phone"] else "empty"
                try:
                    urn = URN.normalize(URN.from_tel(tel), channel.country.code)

                    if "msg" in cmd:
                        msg = Msg.create_relayer_incoming(channel.org, channel, urn, cmd["msg"], date)
                        extra = dict(msg_id=msg.id)
                except ValueError:
                    pass

                handled = True

            # phone event
            elif keyword == "call":
                call_tuple = (cmd["ts"], cmd["type"], cmd["phone"])
                date = datetime.fromtimestamp(int(cmd["ts"]) // 1000).replace(tzinfo=pytz.utc)

                duration = 0
                if cmd["type"] != "miss":
                    duration = cmd["dur"]

                # Android sometimes will pass us a call from an 'unknown number', which is null
                # ignore these events on our side as they have no purpose and break a lot of our
                # assumptions
                if cmd["phone"] and call_tuple not in unique_calls:
                    urn = URN.from_tel(cmd["phone"])
                    try:
                        ChannelEvent.create_relayer_event(
                            channel, urn, cmd["type"], date, extra={"duration": duration}
                        )
                    except ValueError:
                        # in some cases Android passes us invalid URNs, in those cases just ignore them
                        pass

                    unique_calls.add(call_tuple)
                handled = True

            elif keyword == "fcm":
                # update our fcm and uuid

                config = channel.config
                config.update({Channel.CONFIG_FCM_ID: cmd["fcm_id"]})
                channel.config = config
                channel.uuid = cmd.get("uuid", None)
                channel.save(update_fields=["uuid", "config"])

                # no acking the fcm
                handled = False

            elif keyword == "reset":
                # release this channel
                channel.release(channel.modified_by, trigger_sync=False)
                channel.save()

                # ack that things got handled
                handled = True

            elif keyword == "status":
                sync_event = SyncEvent.create(channel, cmd, cmds)
                Alert.check_power_alert(sync_event)

                # tell the channel to update its org if this channel got moved
                if channel.org and "org_id" in cmd and channel.org.pk != cmd["org_id"]:
                    commands.append(dict(cmd="claim", org_id=channel.org.pk))

                # we don't ack status messages since they are always included
                handled = False

        # is this something we can ack?
        if "p_id" in cmd and handled:
            ack = dict(p_id=cmd["p_id"], cmd="ack")
            if extra:
                ack["extra"] = extra

            commands.append(ack)

    outgoing_cmds = get_commands(channel, commands, sync_event)
    result = dict(cmds=outgoing_cmds)

    if sync_event:
        sync_event.outgoing_command_count = len([_ for _ in outgoing_cmds if _["cmd"] != "ack"])
        sync_event.save()

    # keep track of how long a sync takes
    analytics.gauges({"temba.relayer_sync": time.time() - start})

    return JsonResponse(result)


@csrf_exempt
def register(request):
    """
    Endpoint for Android devices registering with this server
    """
    if request.method != "POST":
        return HttpResponse(status=500, content=_("POST Required"))

    client_payload = json.loads(force_str(request.body))
    cmds = client_payload["cmds"]

    try:
        # look up a channel with that id
        channel = Channel.get_or_create_android(cmds[0], cmds[1])
        cmd = dict(
            cmd="reg", relayer_claim_code=channel.claim_code, relayer_secret=channel.secret, relayer_id=channel.id
        )
    except UnsupportedAndroidChannelError:
        cmd = dict(cmd="reg", relayer_claim_code="*********", relayer_secret="0" * 64, relayer_id=-1)

    return JsonResponse(dict(cmds=[cmd]))


class ClaimViewMixin(SpaMixin, OrgPermsMixin, ComponentFormMixin):
    permission = "channels.channel_claim"
    channel_type = None

    class Form(forms.Form):
        def __init__(self, **kwargs):
            self.request = kwargs.pop("request")
            self.channel_type = kwargs.pop("channel_type")
            super().__init__(**kwargs)

        def clean(self):
            count, limit = Channel.get_org_limit_progress(self.request.org)
            if limit is not None and count >= limit:
                raise forms.ValidationError(
                    _(
                        "This workspace has reached its limit of %(limit)d channels. "
                        "You must delete existing ones before you can create new ones."
                    ),
                    params={"limit": limit},
                )
            return super().clean()

    def __init__(self, channel_type):
        self.channel_type = channel_type
        super().__init__()

    def get_template_names(self):
        return (
            [self.template_name]
            if self.template_name
            else ["channels/types/%s/claim.html" % self.channel_type.slug, "channels/channel_claim_form.html"]
        )

    def derive_title(self):
        return _("Connect %(channel_type)s") % {"channel_type": self.channel_type.name}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        kwargs["channel_type"] = self.channel_type
        return kwargs

    def get_success_url(self):
        if self.channel_type.show_config_page:
            return reverse("channels.channel_configuration", args=[self.object.uuid])
        else:
            return reverse("channels.channel_read", args=[self.object.uuid])


class AuthenticatedExternalClaimView(ClaimViewMixin, SmartFormView):
    form_blurb = _("You can connect your number by entering your credentials here.")
    username_label = _("Username")
    username_help = _("The username provided by the provider to use their API")
    password_label = _("Password")
    password_help = _("The password provided by the provider to use their API")

    def __init__(self, **kwargs):
        self.form_blurb = kwargs.pop("form_blurb", self.form_blurb)
        self.username_label = kwargs.pop("username_label", self.username_label)
        self.username_help = kwargs.pop("username_help", self.username_help)
        self.password_label = kwargs.pop("password_label", self.password_label)
        self.password_help = kwargs.pop("password_help", self.password_help)

        super().__init__(**kwargs)

    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES,
            widget=SelectWidget(attrs={"searchable": True}),
            label=_("Country"),
            help_text=_("The country this phone number is used in"),
        )
        number = forms.CharField(
            max_length=14,
            min_length=1,
            label=_("Number"),
            help_text=_("The phone number or short code you are connecting with country code. ex: +250788123124"),
        )
        username = forms.CharField(
            label=_("Username"), help_text=_("The username provided by the provider to use their API")
        )
        password = forms.CharField(
            label=_("Password"), help_text=_("The password provided by the provider to use their API")
        )

        def clean_number(self):
            number = self.data["number"]

            # number is a shortcode, accept as is
            if len(number) > 0 and len(number) < 7:
                return number

            # otherwise, try to parse into an international format
            if number and number[0] != "+":
                number = "+" + number

            try:
                cleaned = phonenumbers.parse(number, None)
                return phonenumbers.format_number(cleaned, phonenumbers.PhoneNumberFormat.E164)
            except Exception:  # pragma: needs cover
                raise forms.ValidationError(
                    _("Invalid phone number, please include the country code. ex: +250788123123")
                )

    form_class = Form

    def lookup_field_label(self, context, field, default=None):
        if field == "password":
            return self.password_label

        elif field == "username":
            return self.username_label

        return super().lookup_field_label(context, field, default=default)

    def lookup_field_help(self, field, default=None):
        if field == "password":
            return self.password_help

        elif field == "username":
            return self.username_help

        return super().lookup_field_help(field, default=default)

    def get_submitted_country(self, data):
        return data["country"]

    def get_channel_config(self, org, data):
        """
        Subclasses can override this method to add in other channel config variables
        """
        return {}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_blurb"] = self.form_blurb
        return context

    def form_valid(self, form):
        org = self.request.org

        data = form.cleaned_data
        extra_config = self.get_channel_config(org, data)
        self.object = Channel.add_authenticated_external_channel(
            org,
            self.request.user,
            self.get_submitted_country(data),
            data["number"],
            data["username"],
            data["password"],
            self.channel_type,
            data.get("url"),
            extra_config=extra_config,
        )

        return super().form_valid(form)


class AuthenticatedExternalCallbackClaimView(AuthenticatedExternalClaimView):
    def get_channel_config(self, org, data):
        return {Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}


class BaseClaimNumberMixin(ClaimViewMixin):
    def pre_process(self, *args, **kwargs):  # pragma: needs cover
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        org = self.request.org

        try:
            context["account_numbers"] = self.get_existing_numbers(org)
        except Exception as e:
            context["account_numbers"] = []
            context["error"] = str(e)

        context["search_url"] = self.get_search_url()
        context["claim_url"] = self.get_claim_url()

        context["search_countries"] = self.get_search_countries()
        context["supported_country_iso_codes"] = self.get_supported_country_iso_codes()

        return context

    def get_search_countries(self):
        search_countries = []

        for country in self.get_search_countries_tuple():
            search_countries.append(dict(key=country[0], label=country[1]))

        return search_countries

    def get_supported_country_iso_codes(self):
        supported_country_iso_codes = []

        for country in self.get_supported_countries_tuple():
            supported_country_iso_codes.append(country[0])

        return supported_country_iso_codes

    def get_search_countries_tuple(self):  # pragma: no cover
        raise NotImplementedError(
            'method "get_search_countries_tuple" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def get_supported_countries_tuple(self):  # pragma: no cover
        raise NotImplementedError(
            'method "get_supported_countries_tuple" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def get_search_url(self):  # pragma: no cover
        raise NotImplementedError(
            'method "get_search_url" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def get_claim_url(self):  # pragma: no cover
        raise NotImplementedError(
            'method "get_claim_url" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def get_existing_numbers(self, org):  # pragma: no cover
        raise NotImplementedError(
            'method "get_existing_numbers" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def is_valid_country(self, calling_code: int) -> bool:  # pragma: no cover
        raise NotImplementedError(
            'method "is_valid_country" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def is_messaging_country(self, country_code: str) -> bool:  # pragma: no cover
        raise NotImplementedError(
            'method "is_messaging_country" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def claim_number(self, user, phone_number, country, role):  # pragma: no cover
        raise NotImplementedError(
            'method "claim_number" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def remove_api_credentials_from_session(self):
        pass

    def form_valid(self, form, *args, **kwargs):

        # must have an org
        org = self.request.org
        if not org:  # pragma: needs cover
            form._errors["upgrade"] = True
            form._errors["phone_number"] = form.error_class(
                [
                    _(
                        "Sorry, you need to have a workspace to add numbers. "
                        "You can still test things out for free using an Android phone."
                    )
                ]
            )
            return self.form_invalid(form)

        data = form.cleaned_data

        # no number parse for short codes
        if len(data["phone_number"]) > 6:
            phone = phonenumbers.parse(data["phone_number"])
            if not self.is_valid_country(phone.country_code):  # pragma: needs cover
                form._errors["phone_number"] = form.error_class(
                    [
                        _(
                            "Sorry, the number you chose is not supported. "
                            "You can still deploy in any country using your "
                            "own SIM card and an Android phone."
                        )
                    ]
                )
                return self.form_invalid(form)

        # don't add the same number twice to the same account
        existing = org.channels.filter(
            is_active=True, address=data["phone_number"], schemes__overlap=list(self.channel_type.schemes)
        ).first()
        if existing:  # pragma: needs cover
            form._errors["phone_number"] = form.error_class(
                [_("That number is already connected (%s)" % data["phone_number"])]
            )
            return self.form_invalid(form)

        existing = Channel.objects.filter(
            is_active=True, address=data["phone_number"], schemes__overlap=list(self.channel_type.schemes)
        ).first()
        if existing:  # pragma: needs cover
            form._errors["phone_number"] = form.error_class(
                [
                    _(
                        "That number is already connected to another account - %(org)s (%(user)s)"
                        % dict(org=existing.org, user=existing.created_by.username)
                    )
                ]
            )
            return self.form_invalid(form)

        error_message = None

        # try to claim the number
        try:
            role = Channel.ROLE_CALL + Channel.ROLE_ANSWER
            if self.is_messaging_country(data["country"]):
                role += Channel.ROLE_SEND + Channel.ROLE_RECEIVE
            self.claim_number(self.request.user, data["phone_number"], data["country"], role)
            self.remove_api_credentials_from_session()

            return HttpResponseRedirect("%s?success" % reverse("public.public_welcome"))

        except (
            nexmo.AuthenticationError,
            nexmo.ClientError,
            twilio.base.exceptions.TwilioRestException,
        ) as e:  # pragma: no cover
            logger.warning(f"Unable to claim a number: {str(e)}", exc_info=True)
            error_message = form.error_class([str(e)])

        except Exception as e:  # pragma: needs cover
            logger.error(f"Unable to claim a number: {str(e)}", exc_info=True)

            message = str(e)
            if message:
                error_message = form.error_class([message])
            else:
                error_message = form.error_class(
                    [
                        _(
                            "An error occurred connecting your Twilio number, try removing your "
                            "Twilio account, reconnecting it and trying again."
                        )
                    ]
                )

        if error_message is not None:
            form._errors["phone_number"] = error_message

        return self.form_invalid(form)


class UpdateChannelForm(forms.ModelForm):
    name = forms.CharField(
        label=_("Name"), max_length=64, required=True, help_text=_("Descriptive name for this channel.")
    )

    def __init__(self, *args, **kwargs):
        self.object = kwargs["object"]
        del kwargs["object"]

        super().__init__(*args, **kwargs)

        self.config_fields = []

        if URN.TEL_SCHEME in self.object.schemes:
            self.add_config_field(
                Channel.CONFIG_ALLOW_INTERNATIONAL,
                forms.BooleanField(required=False, help_text=_("Allow sending to and calling international numbers.")),
                default=False,
            )

        if Channel.ROLE_CALL in self.object.role:
            self.add_config_field(
                Channel.CONFIG_MACHINE_DETECTION,
                forms.BooleanField(
                    required=False, help_text=_("Perform answering machine detection and hangup if machine detected.")
                ),
                default=False,
            )

    def add_config_field(self, config_key: str, field, *, default):
        field.initial = self.instance.config.get(config_key, default)

        self.fields[config_key] = field
        self.config_fields.append(config_key)

    class Meta:
        model = Channel
        fields = ("name", "alert_email")
        readonly = ()
        labels = {}
        helps = {}


class UpdateTelChannelForm(UpdateChannelForm):
    class Meta(UpdateChannelForm.Meta):
        helps = {"address": _("Phone number of this channel")}


class ChannelCRUDL(SmartCRUDL):
    model = Channel
    actions = (
        "list",
        "claim",
        "claim_all",
        "menu",
        "update",
        "read",
        "delete",
        "configuration",
        "bulk_sender_options",
        "create_bulk_sender",
        "create_caller",
        "facebook_whitelist",
    )
    permissions = True

    class Menu(MenuMixin, OrgPermsMixin, SmartTemplateView):  # pragma: no cover
        def derive_menu(self):
            org = self.request.org

            menu = []
            if self.has_org_perm("channels.channel_read"):
                from temba.channels.views import get_channel_read_url

                channels = Channel.objects.filter(org=org, is_active=True, parent=None).order_by("-role")
                for channel in channels:
                    icon = channel.type.icon.replace("icon-", "")
                    icon = icon.replace("power-cord", "box")

                    menu.append(
                        self.create_menu_item(
                            menu_id=channel.uuid,
                            name=channel.name,
                            href=get_channel_read_url(channel),
                            icon=icon,
                        )
                    )

            menu.append(self.create_menu_item(menu_id="claim", name=_("Add Channel"), href="channels.channel_claim"))

            return menu

    class Read(SpaMixin, OrgObjPermsMixin, ContentMenuMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        exclude = ("id", "is_active", "created_by", "modified_by", "modified_on")

        def get_queryset(self):
            return Channel.objects.filter(is_active=True)

        def build_content_menu(self, menu):
            obj = self.get_object()

            for extra in obj.type.extra_links or ():
                menu.add_link(extra["label"], reverse(extra["view_name"], args=[obj.uuid]))

            if obj.parent:
                menu.add_link(_("Android Channel"), reverse("channels.channel_read", args=[obj.parent.uuid]))

            if obj.type.show_config_page:
                menu.add_link(_("Settings"), reverse("channels.channel_configuration", args=[obj.uuid]))

            if not self.is_spa() and not obj.is_android():
                sender = obj.get_sender()
                caller = obj.get_caller()

                if sender:
                    menu.add_link(_("Channel Log"), reverse("channels.channellog_list", args=[sender.uuid]))
                elif Channel.ROLE_RECEIVE in obj.role:
                    menu.add_link(_("Channel Log"), reverse("channels.channellog_list", args=[obj.uuid]))

                if caller and caller != sender:
                    menu.add_link(
                        _("Call Log"), f"{reverse('channels.channellog_list', args=[caller.uuid])}?sessions=1"
                    )

            if self.has_org_perm("channels.channel_update"):
                menu.add_modax(
                    _("Edit"),
                    "update-channel",
                    reverse("channels.channel_update", args=[obj.id]),
                    title=_("Edit Channel"),
                )

                if obj.is_android() or (obj.parent and obj.parent.is_android()):
                    sender = obj.get_sender()

                    if sender and sender.is_delegate_sender():
                        menu.add_modax(
                            _("Disable Bulk Sending"),
                            "disable-sender",
                            reverse("channels.channel_delete", args=[sender.uuid]),
                        )
                    elif obj.is_android():
                        menu.add_link(
                            _("Enable Bulk Sending"),
                            f"{reverse('channels.channel_bulk_sender_options')}?channel={obj.id}",
                        )

                    caller = obj.get_caller()

                    if caller and caller.is_delegate_caller():
                        menu.add_modax(
                            _("Disable Voice Calling"),
                            "disable-voice",
                            reverse("channels.channel_delete", args=[caller.uuid]),
                        )
                    elif obj.org.is_connected_to_twilio():
                        menu.add_url_post(
                            _("Enable Voice Calling"),
                            f"{reverse('channels.channel_create_caller')}?channel={obj.id}",
                        )

            if self.has_org_perm("channels.channel_delete"):
                menu.add_modax(_("Delete"), "delete-channel", reverse("channels.channel_delete", args=[obj.uuid]))

            if obj.channel_type == "FB" and self.has_org_perm("channels.channel_facebook_whitelist"):
                menu.add_modax(
                    _("Whitelist Domain"),
                    "fb-whitelist",
                    reverse("channels.channel_facebook_whitelist", args=[obj.uuid]),
                )

            if self.request.user.is_staff:
                menu.new_group()
                menu.add_url_post(
                    _("Service"),
                    f'{reverse("orgs.org_service")}?organization={obj.org_id}&redirect_url={reverse("channels.channel_read", args=[obj.uuid])}',
                )

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            channel = self.object

            sync_events = SyncEvent.objects.filter(channel=channel.id).order_by("-created_on")
            context["last_sync"] = sync_events.first()

            if "HTTP_X_FORMAX" in self.request.META:  # no additional data needed if request is only for formax
                return context

            if not channel.is_active:  # pragma: needs cover
                raise Http404("No active channel with that id")

            context["msg_count"] = channel.get_msg_count()
            context["ivr_count"] = channel.get_ivr_count()

            # power source stats data
            source_stats = [
                [event["power_source"], event["count"]]
                for event in sync_events.order_by("power_source")
                .values("power_source")
                .annotate(count=Count("power_source"))
            ]
            context["source_stats"] = source_stats

            # network connected to stats
            network_stats = [
                [event["network_type"], event["count"]]
                for event in sync_events.order_by("network_type")
                .values("network_type")
                .annotate(count=Count("network_type"))
            ]
            context["network_stats"] = network_stats

            total_network = 0
            network_share = []

            for net in network_stats:
                total_network += net[1]

            total_share = 0
            for net_stat in network_stats:
                share = int(round((100 * net_stat[1]) / float(total_network)))
                net_name = net_stat[0]

                if net_name != "NONE" and net_name != "UNKNOWN" and share > 0:
                    network_share.append([net_name, share])
                    total_share += share

            other_share = 100 - total_share
            if other_share > 0:
                network_share.append(["OTHER", other_share])

            context["network_share"] = sorted(network_share, key=lambda _: _[1], reverse=True)

            # add to context the latest sync events to display in a table
            context["latest_sync_events"] = sync_events[:10]

            # delayed sync event
            if not channel.is_new():
                if sync_events:
                    latest_sync_event = sync_events[0]
                    interval = timezone.now() - latest_sync_event.created_on
                    seconds = interval.seconds + interval.days * 24 * 3600
                    if seconds > 3600:
                        context["delayed_sync_event"] = latest_sync_event

                # unsent messages
                unsent_msgs = channel.get_delayed_outgoing_messages()

                if unsent_msgs:
                    context["unsent_msgs_count"] = unsent_msgs.count()

            end_date = (timezone.now() + timedelta(days=1)).date()
            start_date = end_date - timedelta(days=30)

            context["start_date"] = start_date
            context["end_date"] = end_date

            message_stats = []

            # build up the channels we care about for outgoing messages
            channels = [channel]
            for sender in Channel.objects.filter(parent=channel):
                channels.append(sender)

            msg_in = []
            msg_out = []
            ivr_in = []
            ivr_out = []

            message_stats.append(dict(name=_("Incoming Text"), data=msg_in))
            message_stats.append(dict(name=_("Outgoing Text"), data=msg_out))

            if context["ivr_count"]:
                message_stats.append(dict(name=_("Incoming IVR"), data=ivr_in))
                message_stats.append(dict(name=_("Outgoing IVR"), data=ivr_out))

            # get all our counts for that period
            daily_counts = list(
                ChannelCount.objects.filter(channel__in=channels, day__gte=start_date)
                .filter(
                    count_type__in=[
                        ChannelCount.INCOMING_MSG_TYPE,
                        ChannelCount.OUTGOING_MSG_TYPE,
                        ChannelCount.INCOMING_IVR_TYPE,
                        ChannelCount.OUTGOING_IVR_TYPE,
                    ]
                )
                .values("day", "count_type")
                .order_by("day", "count_type")
                .annotate(count_sum=Sum("count"))
            )

            current = start_date
            while current <= end_date:
                # for every date we care about
                while daily_counts and daily_counts[0]["day"] == current:
                    daily_count = daily_counts.pop(0)
                    if daily_count["count_type"] == ChannelCount.INCOMING_MSG_TYPE:
                        msg_in.append(dict(date=daily_count["day"], count=daily_count["count_sum"]))
                    elif daily_count["count_type"] == ChannelCount.OUTGOING_MSG_TYPE:
                        msg_out.append(dict(date=daily_count["day"], count=daily_count["count_sum"]))
                    elif daily_count["count_type"] == ChannelCount.INCOMING_IVR_TYPE:
                        ivr_in.append(dict(date=daily_count["day"], count=daily_count["count_sum"]))
                    elif daily_count["count_type"] == ChannelCount.OUTGOING_IVR_TYPE:
                        ivr_out.append(dict(date=daily_count["day"], count=daily_count["count_sum"]))

                current = current + timedelta(days=1)

            context["message_stats"] = message_stats
            context["has_messages"] = len(msg_in) or len(msg_out) or len(ivr_in) or len(ivr_out)

            message_stats_table = []

            # we'll show totals for every month since this channel was started
            month_start = channel.created_on.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            # get our totals grouped by month
            monthly_totals = list(
                ChannelCount.objects.filter(channel=channel, day__gte=month_start)
                .filter(
                    count_type__in=[
                        ChannelCount.INCOMING_MSG_TYPE,
                        ChannelCount.OUTGOING_MSG_TYPE,
                        ChannelCount.INCOMING_IVR_TYPE,
                        ChannelCount.OUTGOING_IVR_TYPE,
                    ]
                )
                .extra({"month": "date_trunc('month', day)"})
                .values("month", "count_type")
                .order_by("month", "count_type")
                .annotate(count_sum=Sum("count"))
            )

            # calculate our summary table for last 12 months
            now = timezone.now()
            while month_start < now:
                msg_in = 0
                msg_out = 0
                ivr_in = 0
                ivr_out = 0

                while monthly_totals and monthly_totals[0]["month"] == month_start:
                    monthly_total = monthly_totals.pop(0)
                    if monthly_total["count_type"] == ChannelCount.INCOMING_MSG_TYPE:
                        msg_in = monthly_total["count_sum"]
                    elif monthly_total["count_type"] == ChannelCount.OUTGOING_MSG_TYPE:
                        msg_out = monthly_total["count_sum"]
                    elif monthly_total["count_type"] == ChannelCount.INCOMING_IVR_TYPE:
                        ivr_in = monthly_total["count_sum"]
                    elif monthly_total["count_type"] == ChannelCount.OUTGOING_IVR_TYPE:
                        ivr_out = monthly_total["count_sum"]

                message_stats_table.append(
                    dict(
                        month_start=month_start,
                        incoming_messages_count=msg_in,
                        outgoing_messages_count=msg_out,
                        incoming_ivr_count=ivr_in,
                        outgoing_ivr_count=ivr_out,
                    )
                )

                month_start = (month_start + timedelta(days=32)).replace(day=1)

            # reverse our table so most recent is first
            message_stats_table.reverse()
            context["message_stats_table"] = message_stats_table

            context["delayed_syncevents"] = not channel.get_recent_syncs().exists()

            return context

    class FacebookWhitelist(ComponentFormMixin, ModalMixin, OrgObjPermsMixin, SmartModelActionView):
        class DomainForm(forms.Form):
            whitelisted_domain = forms.URLField(
                required=True,
                initial="https://",
                help_text="The domain to whitelist for Messenger extensions ex: https://yourdomain.com",
            )

        slug_url_kwarg = "uuid"
        success_url = "uuid@channels.channel_read"
        form_class = DomainForm

        def get_queryset(self):
            return self.request.org.channels.filter(is_active=True, channel_type="FB")

        def execute_action(self):
            # curl -X POST -H "Content-Type: application/json" -d '{
            #  "setting_type" : "domain_whitelisting",
            #  "whitelisted_domains" : ["https://petersfancyapparel.com"],
            #  "domain_action_type": "add"
            # }' "https://graph.facebook.com/v3.3/me/thread_settings?access_token=PAGE_ACCESS_TOKEN"
            access_token = self.object.config[Channel.CONFIG_AUTH_TOKEN]
            response = requests.post(
                "https://graph.facebook.com/v3.3/me/thread_settings?access_token=" + access_token,
                json=dict(
                    setting_type="domain_whitelisting",
                    whitelisted_domains=[self.form.cleaned_data["whitelisted_domain"]],
                    domain_action_type="add",
                ),
            )

            if response.status_code != 200:
                response_json = response.json()
                default_error = dict(message=_("An error occured contacting the Facebook API"))
                raise ValidationError(response_json.get("error", default_error)["message"])

    class Delete(DependencyDeleteModal, SpaMixin):
        cancel_url = "uuid@channels.channel_read"
        success_message = _("Your channel has been removed.")
        success_message_twilio = _(
            "We have disconnected your Twilio number. "
            "If you do not need this number you can delete it from the Twilio website."
        )

        def get_success_url(self):
            # if we're deleting a child channel, redirect to parent afterwards
            channel = self.get_object()
            if channel.parent:
                return reverse("channels.channel_read", args=[channel.parent.uuid])

            return reverse("orgs.org_workspace") if self.is_spa() else reverse("orgs.org_home")

        def derive_submit_button_name(self):
            channel = self.get_object()

            if channel.is_delegate_caller():
                return _("Disable Voice Calling")
            if channel.is_delegate_sender():
                return _("Disable Bulk Sending")

            return super().derive_submit_button_name()

        def post(self, request, *args, **kwargs):
            channel = self.get_object()

            try:
                channel.release(request.user)
            except TwilioRestException as e:
                messages.error(
                    request,
                    _(
                        f"Twilio reported an error removing your channel (error code {e.code}). Please try again later."
                    ),
                )

                response = HttpResponse()
                response["Temba-Success"] = self.cancel_url
                return response

            # override success message for Twilio channels
            if channel.channel_type == "T" and not channel.is_delegate_sender():
                messages.info(request, self.success_message_twilio)
            else:
                messages.info(request, self.success_message)

            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response

    class Update(OrgObjPermsMixin, ComponentFormMixin, ModalMixin, SmartUpdateView):
        success_message = ""
        submit_button_name = _("Save Changes")

        def derive_title(self):
            return _("%s Channel") % self.object.get_channel_type_display()

        def derive_readonly(self):
            return self.form.Meta.readonly if hasattr(self, "form") else []

        def lookup_field_label(self, context, field, default=None):
            if field in self.form.Meta.labels:
                return self.form.Meta.labels[field]
            return super().lookup_field_label(context, field, default=default)

        def lookup_field_help(self, field, default=None):
            if field in self.form.Meta.helps:
                return self.form.Meta.helps[field]
            return super().lookup_field_help(field, default=default)

        def get_success_url(self):
            return reverse("channels.channel_read", args=[self.object.uuid])

        def get_form_class(self):
            return Channel.get_type_from_code(self.object.channel_type).get_update_form()

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["object"] = self.object
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()
            initial["role"] = [char for char in self.object.role]
            return initial

        def pre_save(self, obj):
            for field in self.form.config_fields:
                obj.config[field] = self.form.cleaned_data[field]
            return obj

        def post_save(self, obj):
            # update our delegate channels with the new number
            if not obj.parent and URN.TEL_SCHEME in obj.schemes:
                e164_phone_number = None
                try:
                    parsed = phonenumbers.parse(obj.address, None)
                    e164_phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164).strip(
                        "+"
                    )
                except Exception:  # pragma: needs cover
                    pass
                for channel in obj.get_delegate_channels():  # pragma: needs cover
                    channel.address = obj.address
                    channel.bod = e164_phone_number
                    channel.save(update_fields=("address", "bod"))
            return obj

    class Claim(SpaMixin, OrgPermsMixin, SmartTemplateView):

        title = _("New Channel")

        def channel_types_groups(self):
            org = self.request.org
            user = self.request.user

            # fetch channel types, sorted by category and name
            types_by_category = defaultdict(list)
            recommended_channels = []
            for ch_type in list(Channel.get_types()):
                region_aware_visible, region_ignore_visible = ch_type.is_available_to(org, user)

                if ch_type.is_recommended_to(org, user):
                    recommended_channels.append(ch_type)
                elif region_ignore_visible and region_aware_visible and ch_type.category:
                    types_by_category[ch_type.category.name].append(ch_type)

            return recommended_channels, types_by_category, True

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.request.org

            context["org_timezone"] = str(org.timezone)
            context["brand"] = org.branding

            channel_count, org_limit = Channel.get_org_limit_progress(org)
            context["total_count"] = channel_count
            context["total_limit"] = org_limit

            # fetch channel types, sorted by category and name
            recommended_channels, types_by_category, only_regional_channels = self.channel_types_groups()

            context["recommended_channels"] = recommended_channels
            context["channel_types"] = types_by_category
            context["only_regional_channels"] = only_regional_channels
            return context

    class ClaimAll(Claim):
        def channel_types_groups(self):
            org = self.request.org
            user = self.request.user

            types_by_category = defaultdict(list)
            recommended_channels = []
            for ch_type in list(Channel.get_types()):
                _, region_ignore_visible = ch_type.is_available_to(org, user)
                if ch_type.is_recommended_to(org, user):
                    recommended_channels.append(ch_type)
                elif region_ignore_visible and ch_type.category:
                    types_by_category[ch_type.category.name].append(ch_type)

            return recommended_channels, types_by_category, False

    class BulkSenderOptions(OrgPermsMixin, SmartTemplateView):
        pass

    class CreateBulkSender(OrgPermsMixin, SmartFormView):
        class BulkSenderForm(forms.Form):
            connection = forms.CharField(max_length=2, widget=forms.HiddenInput, required=False)
            channel = forms.IntegerField(widget=forms.HiddenInput, required=False)

            def __init__(self, org, *args, **kwargs):
                self.org = org

                super().__init__(*args, **kwargs)

            def clean_connection(self):
                connection = self.cleaned_data["connection"]
                if connection == "NX" and not self.org.is_connected_to_vonage():
                    raise forms.ValidationError(_("A connection to a Vonage account is required"))
                return connection

            def clean_channel(self):
                channel = self.cleaned_data["channel"]
                channel = self.org.channels.filter(pk=channel).first()
                if not channel:
                    raise forms.ValidationError("Can't add sender for that number")
                return channel

        form_class = BulkSenderForm
        fields = ("connection", "channel")

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["org"] = self.request.org
            return form_kwargs

        def form_valid(self, form):
            channel = form.cleaned_data["channel"]
            Channel.add_vonage_bulk_sender(self.request.org, self.request.user, channel)
            return super().form_valid(form)

        def form_invalid(self, form):
            return super().form_invalid(form)

        def get_success_url(self):
            channel = self.form.cleaned_data["channel"]
            return reverse("channels.channel_read", args=[channel.uuid])

    class CreateCaller(OrgPermsMixin, SmartFormView):
        class CallerForm(forms.Form):
            connection = forms.CharField(max_length=2, widget=forms.HiddenInput, required=False)
            channel = forms.IntegerField(widget=forms.HiddenInput, required=False)

            def __init__(self, *args, **kwargs):
                self.org = kwargs["org"]
                del kwargs["org"]
                super().__init__(*args, **kwargs)

            def clean_connection(self):
                connection = self.cleaned_data["connection"]
                if connection == "T" and not self.org.is_connected_to_twilio():
                    raise forms.ValidationError(_("A connection to a Twilio account is required"))
                return connection

            def clean_channel(self):
                channel = self.cleaned_data["channel"]
                channel = self.org.channels.filter(pk=channel).first()
                if not channel:
                    raise forms.ValidationError(_("A caller cannot be added for that number"))
                if channel.get_caller():
                    raise forms.ValidationError(_("A caller has already been added for that number"))
                return channel

        form_class = CallerForm
        fields = ("connection", "channel")

        def get_form_kwargs(self, *args, **kwargs):
            kwargs = super().get_form_kwargs(*args, **kwargs)
            kwargs["org"] = self.request.org
            return kwargs

        def form_valid(self, form):
            org = self.request.org
            user = self.request.user

            channel = form.cleaned_data["channel"]
            Channel.add_call_channel(org, user, channel)
            return super().form_valid(form)

        def form_invalid(self, form):
            return super().form_invalid(form)

        def get_success_url(self):
            channel = self.form.cleaned_data["channel"]
            return reverse("channels.channel_read", args=[channel.uuid])

    class Configuration(SpaMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["domain"] = self.object.callback_domain
            context["ip_addresses"] = settings.IP_ADDRESSES

            # populate with our channel type
            channel_type = Channel.get_type_from_code(self.object.channel_type)
            context["configuration_template"] = channel_type.get_configuration_template(self.object)
            context["configuration_blurb"] = channel_type.get_configuration_blurb(self.object)
            context["configuration_urls"] = channel_type.get_configuration_urls(self.object)
            context["show_public_addresses"] = channel_type.show_public_addresses

            return context

    class List(OrgPermsMixin, SmartListView):
        title = _("Channels")
        fields = ("name", "address", "last_seen")
        search_fields = ("name", "address", "org__created_by__email")

        def lookup_field_link(self, context, field, obj):
            return reverse("channels.channel_read", args=[obj.uuid])

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).filter(org=self.request.org, is_active=True)

        def pre_process(self, *args, **kwargs):
            # everybody else goes to a different page depending how many channels there are
            channels = list(self.request.org.channels.filter(is_active=True).only("uuid"))

            if len(channels) == 0:
                return HttpResponseRedirect(reverse("channels.channel_claim"))
            elif len(channels) == 1:
                return HttpResponseRedirect(reverse("channels.channel_read", args=[channels[0].uuid]))
            else:
                return super().pre_process(*args, **kwargs)

        def get_name(self, obj):
            return obj.get_name()

        def get_address(self, obj):
            return obj.address if obj.address else _("Unknown")


class ChannelLogCRUDL(SmartCRUDL):
    model = ChannelLog
    path = "logs"  # urls like /channels/logs/
    actions = ("list", "read", "msg", "call")

    class List(SpaMixin, OrgPermsMixin, ContentMenuMixin, SmartListView):
        fields = ("channel", "description", "created_on")
        link_fields = ("channel", "description", "created_on")
        paginate_by = 50

        FOLDER_MESSAGES = "messages"
        FOLDER_CALLS = "calls"
        FOLDER_OTHERS = "others"
        FOLDER_ERRORS = "errors"

        @property
        def folder(self) -> str:
            if self.request.GET.get("calls"):
                return self.FOLDER_CALLS
            elif self.request.GET.get("others"):
                return self.FOLDER_OTHERS
            elif self.request.GET.get("errors"):
                return self.FOLDER_ERRORS
            else:
                return self.FOLDER_MESSAGES

        def build_content_menu(self, menu):
            list_url = reverse("channels.channellog_list", args=[self.channel.uuid])

            if not self.is_spa():
                if self.folder != self.FOLDER_MESSAGES:
                    menu.add_link(_("Messages"), list_url)
                if self.folder != self.FOLDER_CALLS and self.channel.supports_ivr():
                    menu.add_link(_("Calls"), f"{list_url}?calls=1")
                if self.folder != self.FOLDER_OTHERS:
                    menu.add_link(_("Other Interactions"), f"{list_url}?others=1")
                if self.folder != self.FOLDER_ERRORS:
                    menu.add_link(_("Errors"), f"{list_url}?errors=1")

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/(?P<channel_uuid>[^/]+)/$" % path

        def get_template_names(self):
            if self.folder == self.FOLDER_CALLS:
                return ("channels/channellog_calls.haml",)
            else:
                return super().get_template_names()

        @cached_property
        def channel(self):
            return get_object_or_404(Channel, uuid=self.kwargs["channel_uuid"])

        def derive_org(self):
            return self.channel.org

        def derive_queryset(self, **kwargs):
            if self.folder == self.FOLDER_CALLS:
                logs = self.channel.logs.exclude(call=None).values_list("call_id", flat=True)
                events = Call.objects.filter(id__in=logs).order_by("-created_on")

            elif self.folder == self.FOLDER_OTHERS:
                events = self.channel.logs.filter(call=None, msg=None).order_by("-created_on")

            else:
                if self.folder == self.FOLDER_ERRORS:
                    logs = self.channel.logs.filter(call=None, is_error=True)
                else:
                    logs = self.channel.logs.filter(call=None).exclude(msg=None)

                events = logs.order_by("-created_on").select_related(
                    "msg", "msg__contact", "msg__contact_urn", "channel", "channel__org"
                )

                if self.request.GET.get("errors"):
                    patch_queryset_count(events, self.channel.get_error_log_count)
                else:
                    patch_queryset_count(events, self.channel.get_non_ivr_log_count)

            return events

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["channel"] = self.channel
            context["folder"] = self.folder
            return context

    class Read(SpaMixin, OrgObjPermsMixin, ContentMenuMixin, SmartReadView):
        """
        Detail view for a single channel log
        """

        fields = ("description", "created_on")

        def build_content_menu(self, menu):
            obj = self.get_object()
            if not self.is_spa():
                menu.add_link(_("Channel Log"), reverse("channels.channellog_list", args=[obj.channel.uuid]))

        def get_object_org(self):
            return self.get_object().channel.org

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["log"] = self.object.get_display(self.request.user)
            return context

    class Msg(SpaMixin, OrgObjPermsMixin, ContentMenuMixin, SmartListView):
        """
        All channel logs for a message
        """

        permission = "channels.channellog_read"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^(?P<channel_uuid>[0-9a-f-]+)/%s/%s/(?P<msg_id>\d+)/$" % (path, action)

        @cached_property
        def msg(self):
            return get_object_or_404(Msg, id=self.kwargs["msg_id"])

        def build_content_menu(self, menu):
            if not self.is_spa():
                menu.add_link(_("More Logs"), reverse("channels.channellog_list", args=[self.msg.channel.uuid]))

        def get_object_org(self):
            return self.msg.org

        def derive_queryset(self, **kwargs):
            return super().derive_queryset(**kwargs).filter(msg=self.msg).order_by("created_on")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["msg"] = self.msg
            context["logs"] = [log.get_display(self.request.user) for log in context["object_list"]]
            return context

    class Call(SpaMixin, OrgObjPermsMixin, ContentMenuMixin, SmartListView):
        """
        All channel logs for a call
        """

        permission = "channels.channellog_read"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^(?P<channel_uuid>[0-9a-f-]+)/%s/%s/(?P<call_id>\d+)/$" % (path, action)

        @cached_property
        def call(self):
            return get_object_or_404(Call, id=self.kwargs["call_id"])

        def build_content_menu(self, menu):
            menu.add_link(
                _("More Calls"),
                reverse("channels.channellog_list", args=[self.call.channel.uuid]) + "?calls=1",
            )

        def get_object_org(self):
            return self.call.org

        def derive_queryset(self, **kwargs):
            return super().derive_queryset(**kwargs).filter(call=self.call).order_by("created_on")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["call"] = self.call
            context["logs"] = [log.get_display(self.request.user) for log in context["object_list"]]
            return context
