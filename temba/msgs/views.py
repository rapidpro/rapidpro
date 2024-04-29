from datetime import timedelta
from functools import cached_property
from urllib.parse import quote_plus

from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartDeleteView,
    SmartFormView,
    SmartListView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
)

from django import forms
from django.conf import settings
from django.contrib import messages
from django.db.models.functions.text import Lower
from django.forms import Form
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _

from temba.archives.models import Archive
from temba.contacts.search.omnibox import omnibox_deserialize, omnibox_query, omnibox_results_to_dict
from temba.formax import FormaxMixin
from temba.orgs.models import Org
from temba.orgs.views import (
    DependencyDeleteModal,
    DependencyUsagesModal,
    MenuMixin,
    ModalMixin,
    OrgObjPermsMixin,
    OrgPermsMixin,
)
from temba.schedules.models import Schedule
from temba.schedules.views import ScheduleFormMixin
from temba.utils import analytics, json, on_transaction_commit
from temba.utils.export.views import BaseExportView
from temba.utils.fields import CompletionTextarea, InputWidget, JSONField, OmniboxChoice, OmniboxField, SelectWidget
from temba.utils.models import patch_queryset_count
from temba.utils.views import BulkActionMixin, ComponentFormMixin, ContentMenuMixin, SpaMixin, StaffOnlyMixin

from .models import Broadcast, ExportMessagesTask, Label, LabelCount, Media, Msg, SystemLabel
from .tasks import export_messages_task


class MsgListView(SpaMixin, ContentMenuMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
    """
    Base class for message list views with message folders and labels listed by the side
    """

    permission = "msgs.msg_list"
    refresh = 10000
    add_button = True
    system_label = None
    fields = ("from", "message", "received")
    search_fields = ("text__icontains", "contact__name__icontains", "contact__urns__path__icontains")
    paginate_by = 100
    default_order = ("-created_on", "-id")
    allow_export = False
    bulk_actions = ()
    bulk_action_permissions = {"resend": "msgs.broadcast_send", "delete": "msgs.msg_update"}

    def derive_label(self):
        return self.system_label

    def derive_export_url(self):
        redirect = quote_plus(self.request.get_full_path())
        label = self.derive_label()
        label_id = label.uuid if isinstance(label, Label) else label
        return "%s?l=%s&redirect=%s" % (reverse("msgs.msg_export"), label_id, redirect)

    def pre_process(self, request, *args, **kwargs):
        if self.system_label:
            self.queryset = SystemLabel.get_queryset(request.org, self.system_label)

    def get_queryset(self, **kwargs):
        qs = super().get_queryset(**kwargs)

        # if we are searching, limit to last 90, and enforce distinct since we'll be joining on multiple tables
        if "search" in self.request.GET:
            last_90 = timezone.now() - timedelta(days=90)

            # we need to find get the field names we're ordering on without direction
            distinct_on = (f.lstrip("-") for f in self.derive_ordering())

            qs = qs.filter(created_on__gte=last_90).distinct(*distinct_on)

        return qs

    def get_bulk_action_labels(self):
        return self.request.org.msgs_labels.filter(is_active=True)

    def get_context_data(self, **kwargs):
        org = self.request.org
        counts = SystemLabel.get_counts(org)

        label = self.derive_label()

        # if there isn't a search filtering the queryset, we can replace the count function with a pre-calculated value
        if "search" not in self.request.GET:
            if isinstance(label, Label):
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
            dict(count=counts[SystemLabel.TYPE_FAILED], label=_("Failed"), url=reverse("msgs.msg_failed")),
            dict(
                count=counts[SystemLabel.TYPE_SCHEDULED], label=_("Scheduled"), url=reverse("msgs.broadcast_scheduled")
            ),
        ]

        context["org"] = org
        context["folders"] = folders
        context["labels"] = self.get_labels_with_counts(org)
        context["has_messages"] = (
            any(counts.values()) or Archive.objects.filter(org=org, archive_type=Archive.TYPE_MSG).exists()
        )
        context["current_label"] = label
        context["export_url"] = self.derive_export_url()
        context["start_date"] = org.get_delete_date(archive_type=Archive.TYPE_MSG)

        # if refresh was passed in, increase it by our normal refresh time
        previous_refresh = self.request.GET.get("refresh")
        if previous_refresh:
            context["refresh"] = int(previous_refresh) + self.derive_refresh()

        return context

    def get_labels_with_counts(self, org):
        labels = Label.get_active_for_org(org).order_by(Lower("name"))
        label_counts = LabelCount.get_totals([lb for lb in labels])
        for label in labels:
            label.count = label_counts[label]
        return labels

    def build_content_menu(self, menu):
        if self.is_spa():
            if self.has_org_perm("msgs.label_create"):
                menu.add_modax(_("New Label"), "new-msg-label", reverse("msgs.label_create"), title=_("New Label"))

        if self.allow_export and self.has_org_perm("msgs.msg_export"):
            menu.add_modax(_("Download"), "export-messages", self.derive_export_url(), title=_("Download Messages"))


class BroadcastForm(forms.ModelForm):
    message = forms.CharField(
        required=True,
        widget=CompletionTextarea(attrs={"placeholder": _("Hi @contact.name!")}),
        max_length=Broadcast.MAX_TEXT_LEN,
    )

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

    def is_valid(self):
        valid = super().is_valid()
        if valid:
            if "omnibox" not in self.data or len(self.data["omnibox"].strip()) == 0:  # pragma: needs cover
                self.errors["__all__"] = self.error_class([_("At least one recipient is required.")])
                return False

        return valid

    class Meta:
        model = Broadcast
        fields = "__all__"


class BroadcastCRUDL(SmartCRUDL):
    actions = ("scheduled", "scheduled_create", "scheduled_read", "scheduled_update", "scheduled_delete", "send")
    model = Broadcast

    class Scheduled(MsgListView):
        refresh = 30000
        title = _("Scheduled Messages")
        fields = ("contacts", "msgs", "sent", "status")
        search_fields = ("text__icontains", "contacts__urns__path__icontains")
        system_label = SystemLabel.TYPE_SCHEDULED
        default_order = ("-created_on",)

        def build_content_menu(self, menu):
            if self.has_org_perm("msgs.broadcast_scheduled_create"):
                menu.add_modax(
                    _("Schedule Message"),
                    "new-scheduled",
                    reverse("msgs.broadcast_scheduled_create"),
                    title=_("New Scheduled Message"),
                    as_button=True,
                )

        def get_queryset(self, **kwargs):
            return (
                super()
                .get_queryset(**kwargs)
                .filter(is_active=True)
                .select_related("org", "schedule")
                .prefetch_related("groups", "contacts", "urns")
            )

    class ScheduledCreate(OrgPermsMixin, ModalMixin, SmartFormView):
        class Form(ScheduleFormMixin, Form):
            omnibox = OmniboxField(
                label=_("Recipients"),
                required=True,
                help_text=_("The contacts to send the message to"),
                widget=OmniboxChoice(
                    attrs={
                        "placeholder": _("Recipients, enter contacts or groups"),
                        "widget_only": True,
                        "groups": True,
                        "contacts": True,
                        "urns": True,
                    }
                ),
            )
            text = forms.CharField(
                widget=CompletionTextarea(
                    attrs={"placeholder": _("Hi @contact.name!"), "widget_only": True, "counter": "temba-charcount"}
                )
            )

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.set_org(org)
                self.org = org
                self.fields["omnibox"].default_country = org.default_country_code

            def clean_omnibox(self):
                recipients = omnibox_deserialize(self.org, self.cleaned_data["omnibox"])
                if not (recipients["groups"] or recipients["contacts"] or recipients["urns"]):
                    raise forms.ValidationError(_("At least one recipient is required."))
                return recipients

            def clean(self):
                cleaned_data = super().clean()

                ScheduleFormMixin.clean(self)

                return cleaned_data

        form_class = Form
        fields = ("omnibox", "text") + ScheduleFormMixin.Meta.fields
        success_url = "id@msgs.broadcast_scheduled_read"
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def form_valid(self, form):
            user = self.request.user
            org = self.request.org
            text = form.cleaned_data["text"]
            recipients = form.cleaned_data["omnibox"]
            start_time = form.cleaned_data["start_datetime"]
            repeat_period = form.cleaned_data["repeat_period"]
            repeat_days_of_week = form.cleaned_data["repeat_days_of_week"]

            schedule = Schedule.create_schedule(
                org, user, start_time, repeat_period, repeat_days_of_week=repeat_days_of_week
            )
            self.object = Broadcast.create(
                org,
                user,
                text,
                groups=list(recipients["groups"]),
                contacts=list(recipients["contacts"]),
                urns=list(recipients["urns"]),
                status=Msg.STATUS_QUEUED,
                template_state=Broadcast.TEMPLATE_STATE_UNEVALUATED,
                schedule=schedule,
            )

            return self.render_modal_response(form)

    class ScheduledRead(SpaMixin, ContentMenuMixin, FormaxMixin, OrgObjPermsMixin, SmartReadView):
        def derive_title(self):
            return _("Scheduled Message")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["send_history"] = self.get_object().children.order_by("-created_on")
            return context

        def build_content_menu(self, menu):
            obj = self.get_object()

            if self.has_org_perm("msgs.broadcast_scheduled_delete"):
                menu.add_modax(
                    _("Delete"),
                    "delete-scheduled",
                    reverse("msgs.broadcast_scheduled_delete", args=[obj.id]),
                    title=_("Delete Scheduled Message"),
                )

        def derive_formax_sections(self, formax, context):
            if self.has_org_perm("msgs.broadcast_scheduled_update"):
                formax.add_section(
                    "contact", reverse("msgs.broadcast_scheduled_update", args=[self.object.id]), icon="icon-megaphone"
                )

            if self.has_org_perm("schedules.schedule_update"):
                formax.add_section(
                    "schedule",
                    reverse("schedules.schedule_update", args=[self.object.schedule.id]),
                    icon="icon-calendar",
                    action="formax",
                )

    class ScheduledUpdate(OrgObjPermsMixin, ComponentFormMixin, SmartUpdateView):
        form_class = BroadcastForm
        fields = ("message", "omnibox")
        field_config = {"restrict": {"label": ""}, "omnibox": {"label": ""}, "message": {"label": "", "help": ""}}
        success_message = ""
        success_url = "msgs.broadcast_scheduled"

        def derive_initial(self):
            org = self.object.org
            results = [*self.object.groups.all(), *self.object.contacts.all()]
            selected = omnibox_results_to_dict(org, results, version="2")
            message = self.object.text[self.object.base_language]
            return dict(message=message, omnibox=selected)

        def save(self, *args, **kwargs):
            form = self.form
            broadcast = self.object
            org = broadcast.org

            # save off our broadcast info
            omnibox = omnibox_deserialize(org, self.form.cleaned_data["omnibox"])

            # set our new message
            broadcast.text = {broadcast.base_language: form.cleaned_data["message"]}
            broadcast.update_recipients(groups=omnibox["groups"], contacts=omnibox["contacts"], urns=omnibox["urns"])

            broadcast.save()
            return broadcast

    class ScheduledDelete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        default_template = "broadcast_scheduled_delete.haml"
        cancel_url = "id@msgs.broadcast_scheduled_read"
        success_url = "@msgs.broadcast_scheduled"
        fields = ("id",)
        submit_button_name = _("Delete")

        def post(self, request, *args, **kwargs):
            self.get_object().delete(self.request.user, soft=True)

            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response

    class Send(OrgPermsMixin, ModalMixin, SmartFormView):
        class Form(Form):
            omnibox = OmniboxField(
                label=_("Recipients"),
                required=False,
                help_text=_("The contacts to send the message to"),
                widget=OmniboxChoice(
                    attrs={
                        "placeholder": _("Recipients, enter contacts or groups"),
                        "widget_only": True,
                        "groups": True,
                        "contacts": True,
                        "urns": True,
                    }
                ),
            )
            text = forms.CharField(
                widget=CompletionTextarea(
                    attrs={"placeholder": _("Hi @contact.name!"), "widget_only": True, "counter": "temba-charcount"}
                )
            )
            step_node = forms.CharField(widget=forms.HiddenInput, max_length=36, required=False)

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.org = org
                self.fields["omnibox"].default_country = org.default_country_code

            def clean(self):
                cleaned = super().clean()

                if self.is_valid():
                    omnibox = cleaned.get("omnibox")
                    step_node = cleaned.get("step_node")

                    if not step_node and not omnibox:
                        self.add_error("omnibox", _("At least one recipient is required."))

                return cleaned

        form_class = Form
        title = _("Send Message")
        fields = ("omnibox", "text", "step_node")
        success_url = "@msgs.msg_inbox"
        submit_button_name = _("Send")

        blockers = {
            "no_send_channel": _(
                'To get started you need to <a href="%(link)s">add a channel</a> to your workspace which will allow '
                "you to send messages to your contacts."
            ),
        }

        def derive_initial(self):
            initial = super().derive_initial()
            org = self.request.org

            urn_ids = [_ for _ in self.request.GET.get("u", "").split(",") if _]
            contact_uuids = [_ for _ in self.request.GET.get("c", "").split(",") if _]

            if contact_uuids or urn_ids:
                params = {}
                if len(contact_uuids) > 0:
                    params["c"] = ",".join(contact_uuids)
                if len(urn_ids) > 0:
                    params["u"] = ",".join(urn_ids)

                results = omnibox_query(org, **params)
                initial["omnibox"] = omnibox_results_to_dict(org, results, version="2")

            initial["step_node"] = self.request.GET.get("step_node", None)
            return initial

        def derive_fields(self):
            if self.request.GET.get("step_node"):
                return ("text", "step_node")
            else:
                return super().derive_fields()

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["blockers"] = self.get_blockers(self.request.org)
            context["recipient_count"] = int(self.request.GET.get("count", 0))
            return context

        def get_blockers(self, org) -> list:
            blockers = []

            if org.is_suspended:
                blockers.append(Org.BLOCKER_SUSPENDED)
            elif org.is_flagged:
                blockers.append(Org.BLOCKER_FLAGGED)
            if not org.get_send_channel():
                blockers.append(self.blockers["no_send_channel"] % {"link": reverse("channels.channel_claim")})

            return blockers

        def form_valid(self, form):
            user = self.request.user
            org = self.request.org
            step_uuid = form.cleaned_data.get("step_node", None)
            text = form.cleaned_data["text"]

            if step_uuid:
                from .tasks import send_to_flow_node

                get_params = {k: v for k, v in self.request.GET.items()}
                get_params.update({"s": step_uuid})
                send_to_flow_node.delay(org.pk, user.pk, text, **get_params)
            else:
                omnibox = omnibox_deserialize(org, form.cleaned_data["omnibox"])
                groups = list(omnibox["groups"])
                contacts = list(omnibox["contacts"])
                urns = list(omnibox["urns"])

                broadcast = Broadcast.create(
                    org,
                    user,
                    text,
                    groups=groups,
                    contacts=contacts,
                    urns=urns,
                    status=Msg.STATUS_QUEUED,
                    template_state=Broadcast.TEMPLATE_STATE_UNEVALUATED,
                )

                self.post_save(broadcast)
                super().form_valid(form)

                analytics.track(
                    self.request.user,
                    "temba.broadcast_created",
                    dict(contacts=len(contacts), groups=len(groups), urns=len(urns)),
                )

            response = self.render_to_response(self.get_context_data())
            response["Temba-Success"] = "hide"
            return response

        def post_save(self, obj):
            on_transaction_commit(lambda: obj.send_async())
            return obj


class MsgCRUDL(SmartCRUDL):
    model = Msg
    actions = ("inbox", "flow", "archived", "menu", "outbox", "sent", "failed", "filter", "export")

    class Menu(MenuMixin, OrgPermsMixin, SmartTemplateView):  # pragma: no cover
        def derive_menu(self):
            org = self.request.org
            counts = SystemLabel.get_counts(org)

            if self.request.GET.get("labels"):
                labels = Label.get_active_for_org(org).order_by(Lower("name"))
                label_counts = LabelCount.get_totals([lb for lb in labels])

                menu = []
                for label in labels:
                    menu.append(
                        self.create_menu_item(
                            menu_id=label.uuid,
                            name=label.name,
                            href=reverse("msgs.msg_filter", args=[label.uuid]),
                            count=label_counts[label],
                        )
                    )
                return menu
            else:
                labels = Label.get_active_for_org(org).order_by(Lower("name"))

                menu = [
                    self.create_menu_item(
                        name=_("Inbox"),
                        href=reverse("msgs.msg_inbox"),
                        count=counts[SystemLabel.TYPE_INBOX],
                        icon="icon.inbox",
                    ),
                    self.create_menu_item(
                        name=_("Flows"),
                        verbose_name=_("Flow Messages"),
                        href=reverse("msgs.msg_flow"),
                        count=counts[SystemLabel.TYPE_FLOWS],
                        icon="icon.flow",
                    ),
                    self.create_menu_item(
                        name=_("Archived"),
                        verbose_name=_("Archived Messages"),
                        href=reverse("msgs.msg_archived"),
                        count=counts[SystemLabel.TYPE_ARCHIVED],
                        icon="icon.archive",
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        name=_("Outbox"),
                        href=reverse("msgs.msg_outbox"),
                        count=counts[SystemLabel.TYPE_OUTBOX],
                    ),
                    self.create_menu_item(
                        name=_("Sent"),
                        verbose_name=_("Sent Messages"),
                        href=reverse("msgs.msg_sent"),
                        count=counts[SystemLabel.TYPE_SENT],
                    ),
                    self.create_menu_item(
                        name=_("Failed"),
                        verbose_name=_("Failed Messages"),
                        href=reverse("msgs.msg_failed"),
                        count=counts[SystemLabel.TYPE_FAILED],
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        name=_("Scheduled"),
                        verbose_name=_("Scheduled Messages"),
                        href=reverse("msgs.broadcast_scheduled"),
                        count=counts[SystemLabel.TYPE_SCHEDULED],
                    ),
                ]

                label_items = []
                label_counts = LabelCount.get_totals([lb for lb in labels])
                for label in labels:
                    label_items.append(
                        self.create_menu_item(
                            icon="icon.label",
                            menu_id=label.uuid,
                            name=label.name,
                            count=label_counts[label],
                            href=reverse("msgs.msg_filter", args=[label.uuid]),
                        )
                    )

                if label_items:
                    menu.append(self.create_menu_item(name=_("Labels"), items=label_items, inline=True))

                return menu

    class Export(BaseExportView):
        class Form(BaseExportView.Form):
            LABEL_CHOICES = ((0, _("Just this label")), (1, _("All messages")))
            SYSTEM_LABEL_CHOICES = ((0, _("Just this folder")), (1, _("All messages")))

            export_all = forms.ChoiceField(
                choices=(), label=_("Selection"), initial=0, widget=SelectWidget(attrs={"widget_only": True})
            )

            def __init__(self, org, label, *args, **kwargs):
                super().__init__(org, *args, **kwargs)

                self.fields["export_all"].choices = self.LABEL_CHOICES if label else self.SYSTEM_LABEL_CHOICES

        form_class = Form
        success_url = "@msgs.msg_inbox"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["label"] = self.derive_label()[1]
            return kwargs

        def derive_label(self):
            # label is either a UUID of a Label instance (36 chars) or a system label type code (1 char)
            label_id = self.request.GET["l"]
            if len(label_id) == 1:
                return label_id, None
            else:
                return None, Label.get_active_for_org(self.request.org).get(uuid=label_id)

        def get_success_url(self):
            redirect = self.request.GET.get("redirect")
            if redirect and not url_has_allowed_host_and_scheme(redirect, self.request.get_host()):
                redirect = None

            return redirect or reverse("msgs.msg_inbox")

        def form_valid(self, form):
            user = self.request.user
            org = self.request.org

            export_all = bool(int(form.cleaned_data["export_all"]))
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]
            with_fields = form.cleaned_data["with_fields"]
            with_groups = form.cleaned_data["with_groups"]

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
                    start_date=start_date,
                    end_date=end_date,
                    system_label=system_label,
                    label=label,
                    with_fields=with_fields,
                    with_groups=with_groups,
                )

                on_transaction_commit(lambda: export_messages_task.delay(export.id))

                if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):  # pragma: needs cover
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

            response = self.render_modal_response(form)
            response["REDIRECT"] = self.get_success_url()
            return response

    class Inbox(MsgListView):
        title = _("Inbox")
        template_name = "msgs/message_box.haml"
        system_label = SystemLabel.TYPE_INBOX
        bulk_actions = ("archive", "label")
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact", "channel")

    class Flow(MsgListView):
        title = _("Flow Messages")
        template_name = "msgs/message_box.haml"
        system_label = SystemLabel.TYPE_FLOWS
        bulk_actions = ("archive", "label")
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact", "channel")

    class Archived(MsgListView):
        title = _("Archived")
        template_name = "msgs/msg_archived.haml"
        system_label = SystemLabel.TYPE_ARCHIVED
        bulk_actions = ("restore", "label", "delete")
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact", "channel")

    class Outbox(MsgListView):
        title = _("Outbox Messages")
        template_name = "msgs/msg_outbox.haml"
        system_label = SystemLabel.TYPE_OUTBOX
        bulk_actions = ()
        allow_export = True

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            # stuff in any pending broadcasts
            context["pending_broadcasts"] = (
                Broadcast.objects.filter(
                    org=self.request.org,
                    status__in=[Broadcast.STATUS_INITIALIZING, Broadcast.STATUS_QUEUED],
                    schedule=None,
                )
                .select_related("org")
                .prefetch_related("groups", "contacts", "urns")
                .order_by("-created_on")
            )
            return context

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("contact", "channel")

    class Sent(MsgListView):
        title = _("Sent Messages")
        template_name = "msgs/msg_sent.haml"
        system_label = SystemLabel.TYPE_SENT
        bulk_actions = ()
        allow_export = True
        default_order = ("-sent_on", "-id")

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("contact", "channel")

    class Failed(MsgListView):
        title = _("Failed Outgoing Messages")
        template_name = "msgs/msg_failed.haml"
        success_message = ""
        system_label = SystemLabel.TYPE_FAILED
        allow_export = True

        def get_bulk_actions(self):
            return () if self.request.org.is_suspended else ("resend",)

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("contact", "channel")

    class Filter(MsgListView):
        template_name = "msgs/msg_filter.haml"
        bulk_actions = ("label",)

        def derive_title(self, *args, **kwargs):
            return self.label.name

        def build_content_menu(self, menu):
            if self.has_org_perm("msgs.msg_update"):
                menu.add_modax(
                    _("Edit"),
                    "update-label",
                    reverse("msgs.label_update", args=[self.label.id]),
                    title="Edit Label",
                )

            if self.has_org_perm("msgs.msg_export"):
                menu.add_modax(
                    _("Download"), "export-messages", self.derive_export_url(), title=_("Download Messages")
                )

            menu.add_modax(_("Usages"), "label-usages", reverse("msgs.label_usages", args=[self.label.uuid]))

            if self.has_org_perm("msgs.label_delete"):
                menu.add_modax(
                    _("Delete"),
                    "delete-label",
                    reverse("msgs.label_delete", args=[self.label.uuid]),
                    title="Delete Label",
                )

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<label_uuid>[^/]+)/$" % (path, action)

        @cached_property
        def label(self):
            return self.request.org.msgs_labels.get(uuid=self.kwargs["label_uuid"])

        def derive_label(self):
            return self.label

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return (
                qs.filter(labels=self.label, visibility=Msg.VISIBILITY_VISIBLE)
                .prefetch_related("labels")
                .select_related("contact", "channel")
            )


class BaseLabelForm(forms.ModelForm):
    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org

    def clean_name(self):
        name = self.cleaned_data["name"]

        existing_id = self.instance.id if self.instance else None
        if Label.get_active_for_org(self.org).filter(name__iexact=name).exclude(pk=existing_id).exists():
            raise forms.ValidationError(_("Must be unique."))

        count, limit = Label.get_org_limit_progress(self.org)
        if limit is not None and count >= limit:
            raise forms.ValidationError(
                _(
                    "This workspace has reached its limit of %(limit)d labels. "
                    "You must delete existing ones before you can create new ones."
                ),
                params={"limit": limit},
            )

        return name

    class Meta:
        model = Label
        fields = ("name",)
        labels = {"name": _("Name")}
        widgets = {"name": InputWidget()}


class LabelForm(BaseLabelForm):
    messages = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, org, *args, **kwargs):
        super().__init__(org, *args, **kwargs)

    class Meta(BaseLabelForm.Meta):
        fields = ("name",)


class LabelCRUDL(SmartCRUDL):
    model = Label
    actions = ("create", "update", "usages", "delete", "list")

    class List(OrgPermsMixin, SmartListView):
        paginate_by = None
        default_order = ("name",)

        def derive_queryset(self, **kwargs):
            return Label.get_active_for_org(self.request.org)

        def render_to_response(self, context, **response_kwargs):
            results = [{"id": str(lb.uuid), "text": lb.name} for lb in context["object_list"]]
            return HttpResponse(json.dumps(results), content_type="application/json")

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ("name", "messages")
        success_url = "uuid@msgs.msg_filter"
        form_class = LabelForm
        success_message = ""
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def save(self, obj):
            self.object = Label.create(self.request.org, self.request.user, obj.name)

        def post_save(self, obj, *args, **kwargs):
            obj = super().post_save(obj, *args, **kwargs)
            if self.form.cleaned_data["messages"]:  # pragma: needs cover
                msg_ids = [int(m) for m in self.form.cleaned_data["messages"].split(",") if m.isdigit()]
                msgs = Msg.objects.filter(org=obj.org, pk__in=msg_ids)
                if msgs:
                    obj.toggle_label(msgs, add=True)

            return obj

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = LabelForm
        success_url = "uuid@msgs.msg_filter"
        success_message = ""
        title = _("Update Label")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

    class Usages(DependencyUsagesModal):
        permission = "msgs.label_read"

    class Delete(DependencyDeleteModal):
        cancel_url = "@msgs.msg_inbox"
        success_url = "@msgs.msg_inbox"
        success_message = _("Your label has been deleted.")


class MediaCRUDL(SmartCRUDL):
    model = Media
    path = "msgmedia"  # so we don't conflict with the /media directory
    actions = ("upload", "list")

    class Upload(OrgPermsMixin, SmartCreateView):
        def post(self, request, *args, **kwargs):
            file = request.FILES["file"]

            if not Media.is_allowed_type(file.content_type):
                return JsonResponse({"error": _("Unsupported file type")})
            if file.size > Media.MAX_UPLOAD_SIZE:
                limit_MB = Media.MAX_UPLOAD_SIZE / (1024 * 1024)
                return JsonResponse({"error": _("Limit for file uploads is %s MB") % limit_MB})

            media = Media.from_upload(request.org, request.user, file)

            return JsonResponse(
                {
                    "uuid": str(media.uuid),
                    "content_type": media.content_type,
                    "type": media.content_type,  # deprecated
                    "url": media.url,
                    "name": media.filename,
                    "size": media.size,
                }
            )

    class List(StaffOnlyMixin, OrgPermsMixin, SmartListView):
        fields = ("url", "content_type", "size", "created_by", "created_on")
