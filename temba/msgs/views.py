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
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.generic import RedirectView

from temba import mailroom
from temba.archives.models import Archive
from temba.contacts.search import SearchException
from temba.contacts.search.omnibox import omnibox_deserialize, omnibox_query, omnibox_results_to_dict
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
from temba.utils.compose import compose_deserialize, compose_serialize
from temba.utils.export.views import BaseExportView
from temba.utils.fields import (
    CompletionTextarea,
    ComposeField,
    ComposeWidget,
    InputWidget,
    OmniboxChoice,
    OmniboxField,
    SelectWidget,
)
from temba.utils.models import patch_queryset_count
from temba.utils.views import BulkActionMixin, ContentMenuMixin, SpaMixin, StaffOnlyMixin
from temba.utils.wizard import SmartWizardUpdateView, SmartWizardView

from .models import Broadcast, ExportMessagesTask, Label, LabelCount, Media, Msg, SystemLabel
from .tasks import export_messages_task


class SystemLabelView(SpaMixin, OrgPermsMixin, SmartListView):
    """
    Base class for views backed by a system label or message label queryset
    """

    system_label = None
    paginate_by = 100

    def pre_process(self, request, *args, **kwargs):
        if self.system_label:
            self.queryset = SystemLabel.get_queryset(request.org, self.system_label)

    def derive_label(self):
        return self.system_label

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
        context["has_messages"] = (
            any(counts.values()) or Archive.objects.filter(org=org, archive_type=Archive.TYPE_MSG).exists()
        )

        return context


class MsgListView(ContentMenuMixin, BulkActionMixin, SystemLabelView):
    """
    Base class for message list views with message folders and labels listed by the side
    """

    permission = "msgs.msg_list"
    refresh = 10000
    search_fields = ("text__icontains", "contact__name__icontains", "contact__urns__path__icontains")
    default_order = ("-created_on", "-id")
    allow_export = False
    bulk_actions = ()
    bulk_action_permissions = {"resend": "msgs.broadcast_send", "delete": "msgs.msg_update"}

    def derive_export_url(self):
        redirect = quote_plus(self.request.get_full_path())
        label = self.derive_label()
        label_id = label.uuid if isinstance(label, Label) else label
        return "%s?l=%s&redirect=%s" % (reverse("msgs.msg_export"), label_id, redirect)

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

        context = super().get_context_data(**kwargs)
        context["org"] = org
        context["labels"] = Label.get_active_for_org(org).order_by(Lower("name"))

        # if refresh was passed in, increase it by our normal refresh time
        previous_refresh = self.request.GET.get("refresh")
        if previous_refresh:
            context["refresh"] = int(previous_refresh) + self.derive_refresh()

        return context

    def build_content_menu(self, menu):
        if self.has_org_perm("msgs.broadcast_send"):
            menu.add_modax(_("Send Message"), "send-message", reverse("msgs.broadcast_send"), title=_("Send Message"))
        if self.has_org_perm("msgs.label_create"):
            menu.add_modax(_("New Label"), "new-msg-label", reverse("msgs.label_create"), title=_("New Label"))

        if self.allow_export and self.has_org_perm("msgs.msg_export"):
            menu.add_modax(_("Download"), "export-messages", self.derive_export_url(), title=_("Download Messages"))


class ComposeForm(Form):
    compose = ComposeField(
        required=True,
        initial={"text": "", "attachments": []},
        widget=ComposeWidget(attrs={"chatbox": True, "attachments": True, "counter": True}),
    )

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def clean_compose(self):
        compose = self.cleaned_data["compose"]
        text = compose["text"]
        attachments = compose["attachments"]
        if not (text or attachments):
            raise forms.ValidationError(_("Text or attachments are required."))
        if text and len(text) > Msg.MAX_TEXT_LEN:
            raise forms.ValidationError(_(f"Maximum allowed text is {Msg.MAX_TEXT_LEN} characters."))
        if attachments and len(attachments) > Msg.MAX_ATTACHMENTS:
            raise forms.ValidationError(_(f"Maximum allowed attachments is {Msg.MAX_ATTACHMENTS} files."))
        return compose


class ScheduleForm(ScheduleFormMixin):
    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_org(org)


class TargetForm(Form):
    omnibox = OmniboxField(
        label=_("Recipients"),
        required=True,
        help_text=_("The contacts to send the message to."),
        widget=OmniboxChoice(
            attrs={
                "placeholder": _("Search for contacts or groups"),
                "groups": True,
                "contacts": True,
            }
        ),
    )

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org
        self.fields["omnibox"].default_country = org.default_country_code

    def clean_omnibox(self):
        recipients = omnibox_deserialize(self.org, self.cleaned_data["omnibox"])
        if not (recipients["groups"] or recipients["contacts"]):
            raise forms.ValidationError(_("At least one recipient is required."))
        return recipients


class BroadcastCRUDL(SmartCRUDL):
    actions = (
        "create",
        "update",
        "scheduled",
        "scheduled_read",
        "scheduled_delete",
        "preview",
        "send",
    )
    model = Broadcast

    class Create(OrgPermsMixin, SmartWizardView):
        form_list = [("compose", ComposeForm), ("target", TargetForm), ("schedule", ScheduleForm)]
        success_url = "@msgs.broadcast_scheduled"
        submit_button_name = _("Create Broadcast")

        def get_form_kwargs(self, step):
            return {"org": self.request.org}

        def done(self, form_list, form_dict, **kwargs):
            user = self.request.user
            org = self.request.org

            compose = form_dict["compose"].cleaned_data["compose"]
            text, attachments = compose_deserialize(compose)

            recipients = form_dict["target"].cleaned_data["omnibox"]

            schedule_form = form_dict["schedule"]
            start_time = schedule_form.cleaned_data["start_datetime"]
            repeat_period = schedule_form.cleaned_data["repeat_period"]
            repeat_days_of_week = schedule_form.cleaned_data["repeat_days_of_week"]

            schedule = Schedule.create_schedule(
                org, user, start_time, repeat_period, repeat_days_of_week=repeat_days_of_week
            )

            self.object = Broadcast.create(
                org,
                user,
                text={"und": text},
                attachments={"und": attachments},
                groups=list(recipients["groups"]),
                contacts=list(recipients["contacts"]),
                schedule=schedule,
            )

            return HttpResponseRedirect(self.get_success_url())

    class Update(OrgObjPermsMixin, SmartWizardUpdateView):
        form_list = [("compose", ComposeForm), ("target", TargetForm), ("schedule", ScheduleForm)]
        success_url = "id@msgs.broadcast_scheduled_read"
        template_name = "msgs/broadcast_create.html"
        submit_button_name = _("Save Broadcast")

        def get_form_kwargs(self, step):
            return {"org": self.request.org}

        def get_form_initial(self, step):
            org = self.request.org

            if step == "compose":
                translation = self.object.get_translation()
                compose = compose_serialize(translation)
                return {"compose": compose}

            if step == "target":
                recipients = [*self.object.groups.all(), *self.object.contacts.all()]
                omnibox = omnibox_results_to_dict(org, recipients)
                return {"omnibox": omnibox}

            if step == "schedule":
                schedule = self.object.schedule
                return {
                    "start_datetime": schedule.next_fire,
                    "repeat_period": schedule.repeat_period,
                    "repeat_days_of_week": list(schedule.repeat_days_of_week) if schedule.repeat_days_of_week else [],
                }

        def done(self, form_list, form_dict, **kwargs):
            broadcast = self.object
            schedule = broadcast.schedule

            # update message
            compose = form_dict["compose"].cleaned_data["compose"]
            text, attachments = compose_deserialize(compose)
            broadcast.translations = {broadcast.base_language: {"text": text, "attachments": attachments}}

            # update recipients
            recipients = form_dict["target"].cleaned_data["omnibox"]
            broadcast.update_recipients(**recipients)

            # finally, update schedule
            schedule_form = form_dict["schedule"]
            start_time = schedule_form.cleaned_data["start_datetime"]
            repeat_period = schedule_form.cleaned_data["repeat_period"]
            repeat_days_of_week = schedule_form.cleaned_data["repeat_days_of_week"]
            schedule.update_schedule(
                self.request.user, start_time, repeat_period, repeat_days_of_week=repeat_days_of_week
            )

            broadcast.save()

            return HttpResponseRedirect(self.get_success_url())

    class Scheduled(MsgListView):
        title = _("Broadcasts")
        refresh = 30000
        fields = ("contacts", "msgs", "sent", "status")
        search_fields = ("translations__und__icontains", "contacts__urns__path__icontains")
        system_label = SystemLabel.TYPE_SCHEDULED
        default_order = (
            "schedule__next_fire",
            "-created_on",
        )
        menu_path = "/msg/broadcasts"

        def build_content_menu(self, menu):
            if self.has_org_perm("msgs.broadcast_create"):
                menu.add_modax(
                    _("New Broadcast"),
                    "new-scheduled",
                    reverse("msgs.broadcast_create"),
                    title=_("New Broadcast"),
                    as_button=True,
                )

        def get_queryset(self, **kwargs):
            return (
                super()
                .get_queryset(**kwargs)
                .filter(is_active=True)
                .select_related("org", "schedule")
                .prefetch_related("groups", "contacts")
            )

    class ScheduledRead(SpaMixin, ContentMenuMixin, OrgObjPermsMixin, SmartReadView):
        title = _("Broadcast")
        menu_path = "/msg/broadcasts"

        def derive_title(self):
            return _("Broadcast")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["send_history"] = self.get_object().children.order_by("-created_on")
            return context

        def build_content_menu(self, menu):
            obj = self.get_object()

            if self.has_org_perm("msgs.broadcast_update"):
                menu.add_modax(
                    _("Edit"),
                    "edit-broadcast",
                    reverse("msgs.broadcast_update", args=[obj.id]),
                    title=_("Edit Broadcast"),
                )

            if self.has_org_perm("msgs.broadcast_scheduled_delete"):
                menu.add_modax(
                    _("Delete"),
                    "delete-scheduled",
                    reverse("msgs.broadcast_scheduled_delete", args=[obj.id]),
                    title=_("Delete Broadcast"),
                )

    class ScheduledDelete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        default_template = "broadcast_scheduled_delete.html"
        cancel_url = "id@msgs.broadcast_scheduled_read"
        success_url = "@msgs.broadcast_scheduled"
        fields = ("id",)
        submit_button_name = _("Delete")

        def post(self, request, *args, **kwargs):
            self.get_object().delete(self.request.user, soft=True)

            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response

    class Preview(OrgPermsMixin, SmartCreateView):
        permission = "msgs.broadcast_send"

        blockers = {
            "no_send_channel": _(
                'To get started you need to <a href="%(link)s">add a channel</a> to your workspace which will allow '
                "you to send messages to your contacts."
            ),
        }

        def get_blockers(self, org) -> list:
            blockers = []

            if org.is_suspended:
                blockers.append(Org.BLOCKER_SUSPENDED)
            elif org.is_flagged:
                blockers.append(Org.BLOCKER_FLAGGED)
            if not org.get_send_channel():
                blockers.append(self.blockers["no_send_channel"] % {"link": reverse("channels.channel_claim")})

            return blockers

        def post(self, request, *args, **kwargs):
            payload = json.loads(request.body)
            include = mailroom.Inclusions(**payload.get("include", {}))
            exclude = mailroom.Exclusions(**payload.get("exclude", {}))

            try:
                query, total = Broadcast.preview(self.request.org, include=include, exclude=exclude)
            except SearchException as e:
                return JsonResponse({"query": "", "total": 0, "error": str(e)}, status=400)

            return JsonResponse(
                {
                    "query": query,
                    "total": total,
                    "warnings": [],
                    "blockers": self.get_blockers(self.request.org),
                }
            )

    class Send(OrgPermsMixin, ModalMixin, SmartFormView):
        class Form(Form):
            omnibox = OmniboxField(
                label=_("Recipients"),
                required=False,
                help_text=_("The contacts to send the message to."),
                widget=OmniboxChoice(
                    attrs={
                        "placeholder": _("Search for contacts or groups"),
                        "widget_only": True,
                        "groups": True,
                        "contacts": True,
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
            contact_uuids = [_ for _ in self.request.GET.get("c", "").split(",") if _]

            if contact_uuids:
                params = {}
                if len(contact_uuids) > 0:
                    params["c"] = ",".join(contact_uuids)

                results = omnibox_query(org, **params)
                initial["omnibox"] = omnibox_results_to_dict(org, results)

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

                broadcast = Broadcast.create(
                    org, user, {"und": text}, groups=groups, contacts=contacts, status=Msg.STATUS_QUEUED
                )

                self.post_save(broadcast)
                super().form_valid(form)

                analytics.track(
                    self.request.user, "temba.broadcast_created", dict(contacts=len(contacts), groups=len(groups))
                )

            response = self.render_to_response(self.get_context_data())
            response["Temba-Success"] = "hide"
            return response

        def post_save(self, obj):
            on_transaction_commit(lambda: obj.send_async())
            return obj


class MsgCRUDL(SmartCRUDL):
    model = Msg
    actions = ("inbox", "flow", "archived", "menu", "outbox", "sent", "failed", "filter", "export", "legacy_inbox")

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
                        menu_id="inbox",
                        name="Inbox",
                        href=reverse("msgs.msg_inbox"),
                        count=counts[SystemLabel.TYPE_INBOX],
                        icon="inbox",
                    ),
                    self.create_menu_item(
                        menu_id="handled",
                        name="Handled",
                        href=reverse("msgs.msg_flow"),
                        count=counts[SystemLabel.TYPE_FLOWS],
                        icon="flow",
                    ),
                    self.create_menu_item(
                        menu_id="archived",
                        name="Archived",
                        href=reverse("msgs.msg_archived"),
                        count=counts[SystemLabel.TYPE_ARCHIVED],
                        icon="archive",
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        menu_id="outbox",
                        name="Outbox",
                        href=reverse("msgs.msg_outbox"),
                        count=counts[SystemLabel.TYPE_OUTBOX] + Broadcast.get_queued(org).count(),
                    ),
                    self.create_menu_item(
                        menu_id="sent",
                        name="Sent",
                        href=reverse("msgs.msg_sent"),
                        count=counts[SystemLabel.TYPE_SENT],
                    ),
                    self.create_menu_item(
                        menu_id="failed",
                        name="Failed",
                        href=reverse("msgs.msg_failed"),
                        count=counts[SystemLabel.TYPE_FAILED],
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        menu_id="broadcasts",
                        name="Broadcasts",
                        href=reverse("msgs.broadcast_scheduled"),
                        count=counts[SystemLabel.TYPE_SCHEDULED],
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        menu_id="calls",
                        name="Calls",
                        href=reverse("ivr.call_list"),
                        count=counts[SystemLabel.TYPE_CALLS],
                    ),
                ]

                label_items = []
                label_counts = LabelCount.get_totals([lb for lb in labels])
                for label in labels:
                    label_items.append(
                        self.create_menu_item(
                            icon="label",
                            menu_id=label.uuid,
                            name=label.name,
                            count=label_counts[label],
                            href=reverse("msgs.msg_filter", args=[label.uuid]),
                        )
                    )

                if label_items:
                    menu.append(self.create_menu_item(menu_id="labels", name="Labels", items=label_items, inline=True))

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
                        _("Export complete, you can find it here: %s (production users " "will get an email)") % dl_url,
                    )

            messages.success(self.request, self.derive_success_message())

            response = self.render_modal_response(form)
            response["REDIRECT"] = self.get_success_url()
            return response

    class LegacyInbox(RedirectView):
        url = "/msg"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/inbox/$" % (path)

    class Inbox(MsgListView):
        title = _("Inbox Messages")
        template_name = "msgs/message_box.html"
        system_label = SystemLabel.TYPE_INBOX
        bulk_actions = ("archive", "label")
        allow_export = True
        menu_path = "/msg/inbox"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/$" % (path)

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact", "channel")

    class Flow(MsgListView):
        title = _("Flow Messages")
        template_name = "msgs/message_box.html"
        system_label = SystemLabel.TYPE_FLOWS
        bulk_actions = ("archive", "label")
        allow_export = True
        menu_path = "/msg/handled"

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact", "channel", "flow")

    class Archived(MsgListView):
        title = _("Archived Messages")
        template_name = "msgs/msg_archived.html"
        system_label = SystemLabel.TYPE_ARCHIVED
        bulk_actions = ("restore", "label", "delete")
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact", "channel", "flow")

    class Outbox(MsgListView):
        title = _("Outbox Messages")
        template_name = "msgs/msg_outbox.html"
        system_label = SystemLabel.TYPE_OUTBOX
        bulk_actions = ()
        allow_export = True

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            # stuff in any queued broadcasts
            context["queued_broadcasts"] = (
                Broadcast.get_queued(self.request.org)
                .select_related("org")
                .prefetch_related("groups", "contacts")
                .order_by("-created_on")
            )
            return context

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("contact", "channel", "flow")

    class Sent(MsgListView):
        title = _("Sent Messages")
        template_name = "msgs/msg_sent.html"
        system_label = SystemLabel.TYPE_SENT
        bulk_actions = ()
        allow_export = True
        default_order = ("-sent_on", "-id")

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("contact", "channel", "flow")

    class Failed(MsgListView):
        title = _("Failed Messages")
        template_name = "msgs/msg_failed.html"
        success_message = ""
        system_label = SystemLabel.TYPE_FAILED
        allow_export = True

        def get_bulk_actions(self):
            return () if self.request.org.is_suspended else ("resend",)

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("contact", "channel", "flow")

    class Filter(MsgListView):
        template_name = "msgs/msg_filter.html"
        bulk_actions = ("label",)

        def derive_menu_path(self):
            return f"/msg/labels/{self.label.uuid}"

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
                menu.add_modax(_("Download"), "export-messages", self.derive_export_url(), title=_("Download Messages"))

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
                .select_related("contact", "channel", "flow")
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
        """
        TODO deprecated, migrate usages to /api/v2/media.json
        """

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
                    "type": media.content_type,
                    "url": media.url,
                    "name": media.filename,
                    "size": media.size,
                }
            )

    class List(StaffOnlyMixin, OrgPermsMixin, SmartListView):
        fields = ("url", "content_type", "size", "created_by", "created_on")
        default_order = ("-created_on",)

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).filter(org=self.request.org, original=None)
