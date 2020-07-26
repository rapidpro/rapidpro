from datetime import date, timedelta

from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartDeleteView,
    SmartFormView,
    SmartListView,
    SmartReadView,
    SmartUpdateView,
)

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.forms import Form
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import is_safe_url, urlquote_plus
from django.utils.translation import ugettext_lazy as _

from temba.archives.models import Archive
from temba.channels.models import Channel
from temba.contacts.fields import OmniboxField
from temba.contacts.models import TEL_SCHEME, ContactGroup, ContactURN
from temba.contacts.omnibox import omnibox_deserialize, omnibox_query, omnibox_results_to_dict
from temba.flows.legacy.expressions import get_function_listing
from temba.formax import FormaxMixin
from temba.orgs.views import ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils import analytics, json, on_transaction_commit
from temba.utils.fields import CheckboxWidget, CompletionTextarea, JSONField, OmniboxChoice
from temba.utils.models import patch_queryset_count
from temba.utils.views import BulkActionMixin

from .models import INITIALIZING, QUEUED, Broadcast, ExportMessagesTask, Label, Msg, Schedule, SystemLabel
from .tasks import export_messages_task


def send_message_auto_complete_processor(request):
    """
    Adds completions for the expression auto-completion to the request context
    """
    completions = []
    user = request.user
    org = None

    if hasattr(user, "get_org"):
        org = request.user.get_org()

    if org:
        completions.append(dict(name="contact", display=str(_("Contact Name"))))
        completions.append(dict(name="contact.first_name", display=str(_("Contact First Name"))))
        completions.append(dict(name="contact.groups", display=str(_("Contact Groups"))))
        completions.append(dict(name="contact.language", display=str(_("Contact Language"))))
        completions.append(dict(name="contact.name", display=str(_("Contact Name"))))
        completions.append(dict(name="contact.tel", display=str(_("Contact Phone"))))
        completions.append(dict(name="contact.tel_e164", display=str(_("Contact Phone - E164"))))
        completions.append(dict(name="contact.uuid", display=str(_("Contact UUID"))))

        completions.append(dict(name="date", display=str(_("Current Date and Time"))))
        completions.append(dict(name="date.now", display=str(_("Current Date and Time"))))
        completions.append(dict(name="date.today", display=str(_("Current Date"))))
        completions.append(dict(name="date.tomorrow", display=str(_("Tomorrow's Date"))))
        completions.append(dict(name="date.yesterday", display=str(_("Yesterday's Date"))))

        for scheme, label in ContactURN.SCHEME_CHOICES:
            if scheme != TEL_SCHEME and scheme in org.get_schemes(Channel.ROLE_SEND):
                completions.append(dict(name="contact.%s" % scheme, display=str(_("Contact %s" % label))))

        for field in org.contactfields(manager="user_fields").filter(is_active=True).order_by("label"):
            display = str(_("Contact Field: %(label)s")) % {"label": field.label}
            completions.append(dict(name="contact.%s" % str(field.key), display=display))

    function_completions = get_function_listing()
    return dict(completions=json.dumps(completions), function_completions=json.dumps(function_completions))


class SendMessageForm(Form):

    omnibox = JSONField(
        label=_("Recipients"),
        required=False,
        help_text=_("The contacts to send the message to"),
        widget=OmniboxChoice(
            attrs={
                "placeholder": _("Recipients, enter contacts or groups"),
                "groups": True,
                "contacts": True,
                "urns": True,
            }
        ),
    )

    text = forms.CharField(
        widget=CompletionTextarea(attrs={"placeholder": _("Hi @contact.name!"), "widget_only": True})
    )

    schedule = forms.BooleanField(
        widget=CheckboxWidget(attrs={"widget_only": True}),
        required=False,
        label=_("Schedule for later"),
        help_text=None,
    )
    step_node = forms.CharField(widget=forms.HiddenInput, max_length=36, required=False)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def is_valid(self):
        valid = super().is_valid()
        if valid:
            if ("step_node" not in self.data or not self.data["step_node"]) and (
                "omnibox" not in self.data or len(self.data["omnibox"].strip()) == 0
            ):
                self.errors["__all__"] = self.error_class([str(_("At least one recipient is required"))])
                return False
        return valid

    def clean(self):
        cleaned = super().clean()
        org = self.user.get_org()

        if org.is_suspended:
            raise ValidationError(
                _("Sorry, your account is currently suspended. To enable sending messages, please contact support.")
            )
        if org.is_flagged:
            raise ValidationError(
                _("Sorry, your account is currently flagged. To enable sending messages, please contact support.")
            )
        return cleaned


class InboxView(OrgPermsMixin, BulkActionMixin, SmartListView):
    """
    Base class for inbox views with message folders and labels listed by the side
    """

    refresh = 10000
    add_button = True
    system_label = None
    fields = ("from", "message", "received")
    search_fields = ("text__icontains", "contact__name__icontains", "contact__urns__path__icontains")
    paginate_by = 100
    allow_export = False
    show_channel_logs = False
    bulk_actions = ()
    bulk_action_permissions = {"resend": "msgs.broadcast_send", "delete": "msgs.msg_update"}

    def derive_label(self):
        return self.system_label

    def derive_export_url(self):
        redirect = urlquote_plus(self.request.get_full_path())
        label = self.derive_label()
        label_id = label.uuid if isinstance(label, Label) else label
        return "%s?l=%s&redirect=%s" % (reverse("msgs.msg_export"), label_id, redirect)

    def pre_process(self, request, *args, **kwargs):
        if self.system_label:
            org = request.user.get_org()
            self.queryset = SystemLabel.get_queryset(org, self.system_label)

    def get_queryset(self, **kwargs):
        queryset = super().get_queryset(**kwargs)

        # if we are searching, limit to last 90
        if "search" in self.request.GET:
            last_90 = timezone.now() - timedelta(days=90)
            queryset = queryset.filter(created_on__gte=last_90)

        return queryset.order_by("-created_on", "-id").distinct("created_on", "id")

    def get_bulk_action_labels(self):
        return self.get_user().get_org().msgs_labels.all()

    def get_context_data(self, **kwargs):
        org = self.request.user.get_org()
        counts = SystemLabel.get_counts(org)

        label = self.derive_label()

        # if there isn't a search filtering the queryset, we can replace the count function with a pre-calculated value
        if "search" not in self.request.GET:
            if isinstance(label, Label) and not label.is_folder():
                patch_queryset_count(self.object_list, label.get_visible_count)
            elif isinstance(label, str):
                patch_queryset_count(self.object_list, lambda: counts[label])

        context = super().get_context_data(**kwargs)

        folders = [
            dict(count=counts[SystemLabel.TYPE_INBOX], label=_("Inbox"), url=reverse("msgs.msg_inbox")),
            dict(count=counts[SystemLabel.TYPE_FLOWS], label=_("Flows"), url=reverse("msgs.msg_flow")),
            dict(count=counts[SystemLabel.TYPE_ARCHIVED], label=_("Archived"), url=reverse("msgs.msg_archived")),
            dict(count=counts[SystemLabel.TYPE_OUTBOX], label=_("Outbox"), url=reverse("msgs.msg_outbox")),
            dict(count=counts[SystemLabel.TYPE_SENT], label=_("Sent"), url=reverse("msgs.msg_sent")),
            dict(count=counts[SystemLabel.TYPE_CALLS], label=_("Calls"), url=reverse("channels.channelevent_calls")),
            dict(
                count=counts[SystemLabel.TYPE_SCHEDULED],
                label=_("Schedules"),
                url=reverse("msgs.broadcast_schedule_list"),
            ),
            dict(count=counts[SystemLabel.TYPE_FAILED], label=_("Failed"), url=reverse("msgs.msg_failed")),
        ]

        context["org"] = org
        context["folders"] = folders
        context["labels"] = Label.get_hierarchy(org)
        context["has_messages"] = (
            any(counts.values()) or Archive.objects.filter(org=org, archive_type=Archive.TYPE_MSG).exists()
        )
        context["send_form"] = SendMessageForm(self.request.user)
        context["current_label"] = label
        context["export_url"] = self.derive_export_url()
        context["show_channel_logs"] = self.show_channel_logs
        context["start_date"] = org.get_delete_date(archive_type=Archive.TYPE_MSG)

        # if refresh was passed in, increase it by our normal refresh time
        previous_refresh = self.request.GET.get("refresh")
        if previous_refresh:
            context["refresh"] = int(previous_refresh) + self.derive_refresh()

        return context

    def get_gear_links(self):
        links = []
        if self.allow_export and self.has_org_perm("msgs.msg_export"):
            links.append(dict(title=_("Export"), href="#", js_class="msg-export-btn"))
        return links


class BroadcastForm(forms.ModelForm):
    message = forms.CharField(
        required=True,
        widget=CompletionTextarea(attrs={"placeholder": _("Hi @contact.name!")}),
        max_length=Broadcast.MAX_TEXT_LEN,
    )

    omnibox = OmniboxField()

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["omnibox"].set_user(user)

    def is_valid(self):
        valid = super().is_valid()
        if valid:
            if "omnibox" not in self.data or len(self.data["omnibox"].strip()) == 0:  # pragma: needs cover
                self.errors["__all__"] = self.error_class([_("At least one recipient is required")])
                return False

        return valid

    class Meta:
        model = Broadcast
        fields = "__all__"


class BroadcastCRUDL(SmartCRUDL):
    actions = ("send", "update", "schedule_read", "schedule_list")
    model = Broadcast

    class ScheduleRead(FormaxMixin, OrgObjPermsMixin, SmartReadView):
        title = _("Schedule Message")

        def derive_title(self):
            return _("Scheduled Message")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["object_list"] = self.get_object().children.all()
            return context

        def derive_formax_sections(self, formax, context):
            if self.has_org_perm("msgs.broadcast_update"):
                formax.add_section(
                    "contact", reverse("msgs.broadcast_update", args=[self.object.pk]), icon="icon-megaphone"
                )

            if self.has_org_perm("schedules.schedule_update"):
                formax.add_section(
                    "schedule",
                    reverse("schedules.schedule_update", args=[self.object.schedule.pk]),
                    icon="icon-calendar",
                    action="formax",
                )

    class Update(OrgObjPermsMixin, SmartUpdateView):
        form_class = BroadcastForm
        fields = ("message", "omnibox")
        field_config = {"restrict": {"label": ""}, "omnibox": {"label": ""}, "message": {"label": "", "help": ""}}
        success_message = ""
        success_url = "msgs.broadcast_schedule_list"

        def get_form_kwargs(self):
            args = super().get_form_kwargs()
            args["user"] = self.request.user
            return args

        def derive_initial(self):
            selected = ["g-%s" % _.uuid for _ in self.object.groups.all()]
            selected += ["c-%s" % _.uuid for _ in self.object.contacts.all()]
            selected = ",".join(selected)
            message = self.object.text[self.object.base_language]
            return dict(message=message, omnibox=selected)

        def save(self, *args, **kwargs):
            form = self.form
            broadcast = self.object

            # save off our broadcast info
            omnibox = form.cleaned_data["omnibox"]

            # set our new message
            broadcast.text = {broadcast.base_language: form.cleaned_data["message"]}
            broadcast.update_recipients(groups=omnibox["groups"], contacts=omnibox["contacts"], urns=omnibox["urns"])

            broadcast.save()
            return broadcast

    class ScheduleList(InboxView):
        refresh = 30000
        title = _("Scheduled Messages")
        fields = ("contacts", "msgs", "sent", "status")
        search_fields = ("text__icontains", "contacts__urns__path__icontains")
        template_name = "msgs/broadcast_schedule_list.haml"
        default_order = ("schedule__status", "schedule__next_fire", "-created_on")
        system_label = SystemLabel.TYPE_SCHEDULED

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.select_related("schedule").order_by("-created_on")

    class Send(OrgPermsMixin, ModalMixin, SmartFormView):
        title = _("Send Message")
        form_class = SendMessageForm
        fields = ("omnibox", "text", "schedule", "step_node")
        success_url = "@msgs.msg_inbox"
        submit_button_name = _("Send Message")

        def derive_initial(self):
            initial = super().derive_initial()
            org = self.request.user.get_org()

            urn_ids = [_ for _ in self.request.GET.get("u", "").split(",") if _]
            msg_ids = [_ for _ in self.request.GET.get("m", "").split(",") if _]
            contact_uuids = [_ for _ in self.request.GET.get("c", "").split(",") if _]

            if msg_ids or contact_uuids or urn_ids:
                params = {}
                if len(msg_ids) > 0:
                    params["m"] = ",".join(msg_ids)
                if len(contact_uuids) > 0:
                    params["c"] = ",".join(contact_uuids)
                if len(urn_ids) > 0:
                    params["u"] = ",".join(urn_ids)

                results = omnibox_query(org, **params)
                initial["omnibox"] = omnibox_results_to_dict(org, results, version=2)

            initial["step_node"] = self.request.GET.get("step_node", None)
            return initial

        def derive_fields(self):
            if self.request.GET.get("step_node"):
                return ("text", "step_node")
            else:
                return super().derive_fields()

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["recipient_count"] = int(self.request.GET.get("count", 0))
            return context

        def pre_process(self, *args, **kwargs):
            if self.request.method == "POST":
                response = super().pre_process(*args, **kwargs)
                org = self.request.user.get_org()
                # can this org send to any URN schemes?
                if not org.get_schemes(Channel.ROLE_SEND):
                    return HttpResponseBadRequest(_("You must add a phone number before sending messages"))
                return response

        def form_valid(self, form):
            self.form = form
            user = self.request.user
            org = user.get_org()

            step_uuid = self.form.cleaned_data.get("step_node", None)
            text = self.form.cleaned_data["text"]
            has_schedule = False

            if step_uuid:
                from .tasks import send_to_flow_node

                get_params = {k: v for k, v in self.request.GET.items()}
                get_params.update({"s": step_uuid})
                send_to_flow_node.delay(org.pk, user.pk, text, **get_params)
            else:

                omnibox = omnibox_deserialize(org, self.form.cleaned_data["omnibox"])
                has_schedule = self.form.cleaned_data["schedule"]

                groups = list(omnibox["groups"])
                contacts = list(omnibox["contacts"])
                urns = list(omnibox["urns"])

                schedule = Schedule.create_blank_schedule(org, user) if has_schedule else None
                broadcast = Broadcast.create(
                    org,
                    user,
                    text,
                    groups=groups,
                    contacts=contacts,
                    urns=urns,
                    schedule=schedule,
                    status=QUEUED,
                    template_state=Broadcast.TEMPLATE_STATE_UNEVALUATED,
                )

                if not has_schedule:
                    self.post_save(broadcast)
                    super().form_valid(form)

                analytics.track(
                    self.request.user.username,
                    "temba.broadcast_created",
                    dict(contacts=len(contacts), groups=len(groups), urns=len(urns)),
                )

            if "HTTP_X_PJAX" in self.request.META:
                success_url = "hide"
                if has_schedule:
                    success_url = reverse("msgs.broadcast_schedule_read", args=[broadcast.pk])

                response = self.render_to_response(self.get_context_data())
                response["Temba-Success"] = success_url
                return response

            return HttpResponseRedirect(self.get_success_url())

        def post_save(self, obj):
            on_transaction_commit(lambda: obj.send())
            return obj

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs


class TestMessageForm(forms.Form):
    channel = forms.ModelChoiceField(
        Channel.objects.filter(id__lt=0), help_text=_("Which channel will deliver the message")
    )
    urn = forms.CharField(max_length=14, help_text=_("The URN of the contact delivering this message"))
    text = forms.CharField(max_length=160, widget=forms.Textarea, help_text=_("The message that is being delivered"))

    def __init__(self, *args, **kwargs):  # pragma: needs cover
        org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)
        self.fields["channel"].queryset = Channel.objects.filter(org=org, is_active=True)


class ExportForm(Form):
    LABEL_CHOICES = ((0, _("Just this label")), (1, _("All messages")))
    SYSTEM_LABEL_CHOICES = ((0, _("Just this folder")), (1, _("All messages")))

    export_all = forms.ChoiceField(choices=(), label=_("Selection"), initial=0)

    groups = forms.ModelMultipleChoiceField(
        queryset=ContactGroup.user_groups.none(), required=False, label=_("Groups")
    )
    start_date = forms.DateField(
        required=False,
        help_text=_("The date for the oldest message to export. " "(Leave blank to export from the oldest message)."),
    )
    end_date = forms.DateField(
        required=False,
        help_text=_("The date for the latest message to export. " "(Leave blank to export up to the latest message)."),
    )

    def __init__(self, user, label, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        self.fields["export_all"].choices = self.LABEL_CHOICES if label else self.SYSTEM_LABEL_CHOICES

        self.fields["groups"].queryset = ContactGroup.user_groups.filter(org=self.user.get_org(), is_active=True)
        self.fields["groups"].help_text = _(
            "Export only messages from these contact groups. " "(Leave blank to export all messages)."
        )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and start_date > date.today():  # pragma: needs cover
            raise forms.ValidationError(_("Start date can't be in the future."))

        if end_date and start_date and end_date < start_date:  # pragma: needs cover
            raise forms.ValidationError(_("End date can't be before start date"))

        return cleaned_data


class MsgCRUDL(SmartCRUDL):
    model = Msg
    actions = ("inbox", "flow", "archived", "outbox", "sent", "failed", "filter", "export")

    class Export(ModalMixin, OrgPermsMixin, SmartFormView):

        form_class = ExportForm
        submit_button_name = "Export"
        success_url = "@msgs.msg_inbox"

        def derive_label(self):
            # label is either a UUID of a Label instance (36 chars) or a system label type code (1 char)
            label_id = self.request.GET["l"]
            if len(label_id) == 1:
                return label_id, None
            else:
                return None, Label.all_objects.get(org=self.request.user.get_org(), uuid=label_id)

        def get_success_url(self):
            redirect = self.request.GET.get("redirect")
            if redirect and not is_safe_url(redirect, self.request.get_host()):
                redirect = None

            return redirect or reverse("msgs.msg_inbox")

        def form_invalid(self, form):  # pragma: needs cover
            if "_format" in self.request.GET and self.request.GET["_format"] == "json":
                return HttpResponse(
                    json.dumps(dict(status="error", errors=form.errors)), content_type="application/json", status=400
                )
            else:
                return super().form_invalid(form)

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()

            export_all = bool(int(form.cleaned_data["export_all"]))
            groups = form.cleaned_data["groups"]
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]

            system_label, label = (None, None) if export_all else self.derive_label()

            # is there already an export taking place?
            existing = ExportMessagesTask.get_recent_unfinished(org)
            if existing:
                messages.info(
                    self.request,
                    _(
                        "There is already an export in progress, started by %s. You must wait "
                        "for that export to complete before starting another." % existing.created_by.username
                    ),
                )

            # otherwise, off we go
            else:
                export = ExportMessagesTask.create(
                    org,
                    user,
                    system_label=system_label,
                    label=label,
                    groups=groups,
                    start_date=start_date,
                    end_date=end_date,
                )

                on_transaction_commit(lambda: export_messages_task.delay(export.id))

                if not getattr(settings, "CELERY_ALWAYS_EAGER", False):  # pragma: needs cover
                    messages.info(
                        self.request,
                        _("We are preparing your export. We will e-mail you at %s when " "it is ready.")
                        % self.request.user.username,
                    )

                else:
                    dl_url = reverse("assets.download", kwargs=dict(type="message_export", pk=export.pk))
                    messages.info(
                        self.request,
                        _("Export complete, you can find it here: %s (production users " "will get an email)")
                        % dl_url,
                    )

            messages.success(self.request, self.derive_success_message())

            if "HTTP_X_PJAX" not in self.request.META:
                return HttpResponseRedirect(self.get_success_url())
            else:  # pragma: no cover
                response = self.render_to_response(
                    self.get_context_data(
                        form=form,
                        success_url=self.get_success_url(),
                        success_script=getattr(self, "success_script", None),
                    )
                )
                response["Temba-Success"] = self.get_success_url()
                response["REDIRECT"] = self.get_success_url()
                return response

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            kwargs["label"] = self.derive_label()[1]
            return kwargs

    class Inbox(InboxView):
        title = _("Inbox")
        template_name = "msgs/message_box.haml"
        system_label = SystemLabel.TYPE_INBOX
        bulk_actions = ("archive", "label")
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact")

    class Flow(InboxView):
        title = _("Flow Messages")
        template_name = "msgs/message_box.haml"
        system_label = SystemLabel.TYPE_FLOWS
        bulk_actions = ("label",)
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact")

    class Archived(InboxView):
        title = _("Archived")
        template_name = "msgs/msg_archived.haml"
        system_label = SystemLabel.TYPE_ARCHIVED
        bulk_actions = ("restore", "label", "delete")
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact")

    class Outbox(InboxView):
        title = _("Outbox Messages")
        template_name = "msgs/msg_outbox.haml"
        system_label = SystemLabel.TYPE_OUTBOX
        bulk_actions = ()
        allow_export = True
        show_channel_logs = True

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            # stuff in any pending broadcasts
            context["pending_broadcasts"] = (
                Broadcast.objects.filter(
                    org=self.request.user.get_org(), status__in=[QUEUED, INITIALIZING], schedule=None
                )
                .prefetch_related("groups", "contacts", "urns")
                .order_by("-created_on")
            )
            return context

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("channel_logs").select_related("contact")

    class Sent(InboxView):
        title = _("Sent Messages")
        template_name = "msgs/msg_sent.haml"
        system_label = SystemLabel.TYPE_SENT
        bulk_actions = ()
        allow_export = True
        show_channel_logs = True

        def get_queryset(self, **kwargs):  # pragma: needs cover
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("channel_logs").select_related("contact")

    class Failed(InboxView):
        title = _("Failed Outgoing Messages")
        template_name = "msgs/msg_failed.haml"
        success_message = ""
        system_label = SystemLabel.TYPE_FAILED
        bulk_actions = ("resend",)
        allow_export = True
        show_channel_logs = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("channel_logs").select_related("contact")

    class Filter(InboxView):
        template_name = "msgs/msg_filter.haml"
        bulk_actions = ("unlabel", "label")

        def derive_title(self, *args, **kwargs):
            return self.derive_label().name

        def get_gear_links(self):
            links = []

            edit_btn_cls = "folder-update-btn" if self.derive_label().is_folder() else "label-update-btn"

            if self.has_org_perm("msgs.msg_update"):
                links.append(dict(title=_("Edit"), href="#", js_class=edit_btn_cls))

            if self.has_org_perm("msgs.msg_export"):
                links.append(dict(title=_("Export"), href="#", js_class="msg-export-btn"))

            if self.has_org_perm("msgs.broadcast_send"):
                links.append(
                    dict(title=_("Send All"), style="btn-primary", href="#", js_class="filter-send-all-send-button")
                )

            if self.has_org_perm("msgs.label_delete"):
                links.append(dict(title=_("Remove"), href="#", js_class="remove-label"))

            return links

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<label>[^/]+)/$" % (path, action)

        def derive_label(self):
            return self.request.user.get_org().msgs_labels.get(uuid=self.kwargs["label"])

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            qs = self.derive_label().filter_messages(qs).filter(visibility=Msg.VISIBILITY_VISIBLE)

            return qs.prefetch_related("labels").select_related("contact")


class BaseLabelForm(forms.ModelForm):
    def clean_name(self):
        name = self.cleaned_data["name"]

        if not Label.is_valid_name(name):
            raise forms.ValidationError(_("Name must not be blank or begin with punctuation"))

        existing_id = self.existing.pk if self.existing else None
        if Label.all_objects.filter(org=self.org, name__iexact=name).exclude(pk=existing_id).exists():
            raise forms.ValidationError(_("Name must be unique"))

        labels_count = Label.all_objects.filter(org=self.org, is_active=True).count()
        if labels_count >= Label.MAX_ORG_LABELS:
            raise forms.ValidationError(
                _(
                    "This org has %(count)d labels and the limit is %(limit)d. "
                    "You must delete existing ones before you can "
                    "create new ones." % dict(count=labels_count, limit=Label.MAX_ORG_LABELS)
                )
            )

        return name

    class Meta:
        model = Label
        fields = "__all__"


class LabelForm(BaseLabelForm):
    folder = forms.ModelChoiceField(Label.folder_objects.none(), required=False, label=_("Folder"))
    messages = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        self.org = kwargs.pop("org")
        self.existing = kwargs.pop("object", None)

        super().__init__(*args, **kwargs)

        self.fields["folder"].queryset = Label.folder_objects.filter(org=self.org)


class FolderForm(BaseLabelForm):
    name = forms.CharField(label=_("Name"), help_text=_("The name of this folder"))

    def __init__(self, *args, **kwargs):
        self.org = kwargs.pop("org")
        self.existing = kwargs.pop("object", None)

        super().__init__(*args, **kwargs)


class LabelCRUDL(SmartCRUDL):
    model = Label
    actions = ("create", "create_folder", "update", "delete", "list")

    class List(OrgPermsMixin, SmartListView):
        paginate_by = None
        default_order = ("name",)

        def derive_queryset(self, **kwargs):
            return Label.label_objects.filter(org=self.request.user.get_org())

        def render_to_response(self, context, **response_kwargs):
            results = [dict(id=l.uuid, text=l.name) for l in context["object_list"]]
            return HttpResponse(json.dumps(results), content_type="application/json")

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ("name", "folder", "messages")
        success_url = "@msgs.msg_inbox"
        form_class = LabelForm
        success_message = ""
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

        def save(self, obj):
            user = self.request.user
            self.object = Label.get_or_create(user.get_org(), user, obj.name, obj.folder)

        def post_save(self, obj, *args, **kwargs):
            obj = super().post_save(obj, *args, **kwargs)

            if self.form.cleaned_data["messages"]:  # pragma: needs cover
                msg_ids = [int(m) for m in self.form.cleaned_data["messages"].split(",") if m.isdigit()]
                messages = Msg.objects.filter(org=obj.org, pk__in=msg_ids)
                if messages:
                    obj.toggle_label(messages, add=True)

            return obj

    class CreateFolder(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ("name",)
        success_url = "@msgs.msg_inbox"
        form_class = FolderForm
        success_message = ""
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

        def save(self, obj):
            user = self.request.user
            self.object = Label.get_or_create_folder(user.get_org(), user, obj.name)

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        success_url = "uuid@msgs.msg_filter"
        success_message = ""

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            kwargs["object"] = self.get_object()
            return kwargs

        def get_form_class(self):
            return FolderForm if self.get_object().is_folder() else LabelForm

        def derive_title(self):
            return _("Update Folder") if self.get_object().is_folder() else _("Update Label")

        def derive_fields(self):
            return ("name",) if self.get_object().is_folder() else ("name", "folder")

    class Delete(OrgObjPermsMixin, SmartDeleteView):
        redirect_url = "@msgs.msg_inbox"
        cancel_url = "@msgs.msg_inbox"
        success_message = ""

        def post(self, request, *args, **kwargs):
            label = self.get_object()
            label.release(self.request.user)

            return HttpResponseRedirect(self.get_redirect_url())
