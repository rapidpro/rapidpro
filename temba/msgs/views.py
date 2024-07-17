import mimetypes
import os
from datetime import timedelta
from functools import cached_property
from urllib.parse import quote_plus

import magic
from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartDeleteView,
    SmartListView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
)

from django import forms
from django.db.models.functions.text import Lower
from django.forms import Form, ValidationError
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import RedirectView

from temba import mailroom
from temba.archives.models import Archive
from temba.mailroom.client.types import Exclusions
from temba.orgs.models import Org
from temba.orgs.views import (
    BaseExportView,
    DependencyDeleteModal,
    DependencyUsagesModal,
    MenuMixin,
    ModalMixin,
    OrgObjPermsMixin,
    OrgPermsMixin,
)
from temba.schedules.views import ScheduleFormMixin
from temba.templates.models import Template, TemplateTranslation
from temba.utils import json, languages
from temba.utils.compose import compose_deserialize, compose_serialize
from temba.utils.fields import (
    CompletionTextarea,
    ComposeField,
    ComposeWidget,
    ContactSearchWidget,
    InputWidget,
    SelectWidget,
)
from temba.utils.models import patch_queryset_count
from temba.utils.views import BulkActionMixin, ContentMenuMixin, NonAtomicMixin, PostOnlyMixin, SpaMixin, StaffOnlyMixin
from temba.utils.wizard import SmartWizardUpdateView, SmartWizardView

from .models import Broadcast, Label, LabelCount, Media, MessageExport, Msg, OptIn, SystemLabel


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
    search_fields = ("text__icontains", "contact__name__icontains", "contact__urns__path__icontains")
    default_order = ("-created_on", "-id")
    allow_export = False
    bulk_actions = ()
    bulk_action_permissions = {"resend": "msgs.msg_create", "delete": "msgs.msg_update"}

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
        if self.has_org_perm("msgs.broadcast_create"):
            menu.add_modax(
                _("Send"), "send-message", reverse("msgs.broadcast_create"), title=_("New Broadcast"), as_button=True
            )
        if self.has_org_perm("msgs.label_create"):
            menu.add_modax(_("New Label"), "new-msg-label", reverse("msgs.label_create"), title=_("New Label"))

        if self.allow_export and self.has_org_perm("msgs.msg_export"):
            menu.add_modax(_("Export"), "export-messages", self.derive_export_url(), title=_("Export Messages"))


class ComposeForm(Form):
    compose = ComposeField(
        widget=ComposeWidget(
            attrs={
                "chatbox": True,
                "attachments": True,
                "counter": True,
                "completion": True,
                "quickreplies": True,
                "optins": True,
                "templates": True,
            }
        ),
    )

    def clean_compose(self):
        base_language = self.initial.get("base_language", "und")
        primary_language = self.org.flow_languages[0] if self.org.flow_languages else None

        def is_language_missing(values):
            if values:
                text = values.get("text", "")
                attachments = values.get("attachments", [])
                return not (text or attachments)
            return True

        # need at least a base or a primary
        compose = self.cleaned_data["compose"]
        base = compose.get(base_language, None)
        primary = compose.get(primary_language, None)

        if is_language_missing(base) and is_language_missing(primary):
            raise forms.ValidationError(_("This field is required."))

        # check that all of our text and attachments are limited
        # these are also limited client side, so this is a fail safe
        for values in compose.values():
            if values:
                text = values.get("text", "")
                attachments = values.get("attachments", [])
                if text and len(text) > Msg.MAX_TEXT_LEN:
                    raise forms.ValidationError(_(f"Maximum allowed text is {Msg.MAX_TEXT_LEN} characters."))
                if attachments and len(attachments) > Msg.MAX_ATTACHMENTS:
                    raise forms.ValidationError(_(f"Maximum allowed attachments is {Msg.MAX_ATTACHMENTS} files."))

        primaryValues = compose.get(primary_language or base_language, {})
        template = primaryValues.get("template", None)
        locale = primaryValues.get("locale", None)
        variables = primaryValues.get("variables", [])
        if template:
            translation = TemplateTranslation.objects.filter(
                template__org=self.org, template__uuid=template, locale=locale
            ).first()
            if translation:
                for idx, param in enumerate(translation.variables):
                    # non text variables are required
                    if param.get("type") != "text":
                        if idx >= len(variables) or not variables[idx]:
                            raise forms.ValidationError(_("The attachment for the WhatsApp template is required."))

        return compose

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org
        isos = [iso for iso in org.flow_languages]

        if self.initial and "base_language" in self.initial:
            compose = self.initial["compose"]
            base_language = self.initial["base_language"]

            if base_language not in isos:
                # if we have a value for the primary org language show that first
                if isos and isos[0] in compose:
                    isos.append(base_language)
                else:
                    # otherwise, put our base_language first
                    isos.insert(0, base_language)

            # our base language might be a secondary language, see if it should be first
            elif isos[0] not in compose:
                isos.remove(base_language)
                isos.insert(0, base_language)

        langs = [{"iso": iso, "name": str(_("Default")) if iso == "und" else languages.get_name(iso)} for iso in isos]
        compose_attrs = self.fields["compose"].widget.attrs
        compose_attrs["languages"] = json.dumps(langs)


class ScheduleForm(ScheduleFormMixin):
    SEND_NOW = "now"
    SEND_LATER = "later"

    SEND_CHOICES = (
        (SEND_NOW, _("Send right now")),
        (SEND_LATER, _("Schedule for later")),
    )

    send_when = forms.ChoiceField(
        choices=SEND_CHOICES, widget=forms.RadioSelect(attrs={"widget_only": True}), required=False
    )

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["start_datetime"].required = False
        self.set_org(org)

    def clean(self):
        send_when = self.data.get("schedule-send_when", ScheduleForm.SEND_LATER)  # doesn't exist for updates
        start_datetime = self.data.get("schedule-start_datetime")

        if send_when == ScheduleForm.SEND_LATER and not start_datetime:
            raise forms.ValidationError(_("Select when you would like the broadcast to be sent"))

        return super().clean()

    class Meta:
        fields = ScheduleFormMixin.Meta.fields + ("send_when",)


class TargetForm(Form):

    contact_search = forms.JSONField(
        widget=ContactSearchWidget(
            attrs={
                "in_a_flow": True,
                "not_seen_since_days": True,
                "widget_only": True,
                "endpoint": "/broadcast/preview/",
                "placeholder": _("Enter contact query"),
            }
        ),
    )

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org

    def clean_contact_search(self):
        contact_search = self.cleaned_data.get("contact_search")
        recipients = contact_search.get("recipients", [])

        if contact_search["advanced"] and ("query" not in contact_search or not contact_search["query"]):
            raise ValidationError(_("A contact query is required."))

        if not contact_search["advanced"] and len(recipients) == 0:
            raise ValidationError(_("Contacts or groups are required."))

        if contact_search["advanced"]:
            try:
                contact_search["parsed_query"] = (
                    mailroom.get_client().contact_parse_query(self.org, contact_search["query"], parse_only=True).query
                )
            except mailroom.QueryValidationException as e:
                raise ValidationError(str(e))

        return contact_search


class BroadcastCRUDL(SmartCRUDL):
    actions = (
        "list",
        "create",
        "update",
        "scheduled",
        "scheduled_read",
        "scheduled_delete",
        "preview",
        "to_node",
    )
    model = Broadcast

    class List(MsgListView):
        title = _("Broadcasts")
        menu_path = "/msg/broadcasts"
        paginate_by = 25

        def get_queryset(self, **kwargs):
            return (
                super()
                .get_queryset(**kwargs)
                .filter(is_active=True, schedule=None, org=self.request.org)
                .select_related("org", "schedule")
                .prefetch_related("groups", "contacts")
            )

        def build_content_menu(self, menu):
            if self.has_org_perm("msgs.broadcast_create"):
                menu.add_modax(
                    _("Send"),
                    "new-scheduled",
                    reverse("msgs.broadcast_create"),
                    title=_("New Broadcast"),
                    as_button=True,
                )

    class Scheduled(MsgListView):
        title = _("Scheduled Broadcasts")
        menu_path = "/msg/scheduled"
        fields = ("contacts", "msgs", "sent", "status")
        system_label = SystemLabel.TYPE_SCHEDULED
        paginate_by = 25
        default_order = (
            "schedule__next_fire",
            "-created_on",
        )

        def build_content_menu(self, menu):
            if self.has_org_perm("msgs.broadcast_create"):
                menu.add_modax(
                    _("Send"),
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

    class Create(OrgPermsMixin, SmartWizardView):
        form_list = [("target", TargetForm), ("compose", ComposeForm), ("schedule", ScheduleForm)]
        success_url = "@msgs.broadcast_scheduled"
        submit_button_name = _("Create")

        def get_form_kwargs(self, step):
            return {"org": self.request.org}

        def get_form_initial(self, step):

            if step == "target":
                initial = {}
                org = self.request.org
                contact_uuids = [_ for _ in self.request.GET.get("c", "").split(",") if _]
                contacts = org.contacts.filter(uuid__in=contact_uuids)
                if contact_uuids:
                    params = {}
                    if len(contact_uuids) > 0:
                        params["c"] = ",".join(contact_uuids)
                    initial["contact_search"] = {
                        "recipients": ContactSearchWidget.get_recipients(contacts),
                        "advanced": False,
                        "query": None,
                        "exclusions": {},
                    }
                    return initial
            return super().get_form_initial(step)

        def done(self, form_list, form_dict, **kwargs):
            user = self.request.user
            org = self.request.org
            compose = form_dict["compose"].cleaned_data["compose"]
            translations = compose_deserialize(compose)
            base_language = next(iter(translations))
            optin = None
            template = None
            template_variables = []

            # extract template and optin which are packed into the base translation
            for trans in compose.values():
                if trans.get("optin"):
                    optin_ref = trans.pop("optin")
                    optin = OptIn.objects.filter(org=org, uuid=optin_ref["uuid"]).first()
                if trans.get("template"):
                    template = Template.objects.filter(org=org, uuid=trans.pop("template")).first()
                    template_variables = trans.pop("variables", [])

            contact_search = form_dict["target"].cleaned_data["contact_search"]
            schedule_form = form_dict["schedule"]
            send_when = schedule_form.cleaned_data["send_when"]
            schedule = None

            if send_when == ScheduleForm.SEND_LATER:
                start = schedule_form.cleaned_data["start_datetime"].astimezone(org.timezone)
                schedule = mailroom.ScheduleSpec(
                    start=start.isoformat(),
                    repeat_period=schedule_form.cleaned_data["repeat_period"],
                    repeat_days_of_week=schedule_form.cleaned_data["repeat_days_of_week"],
                )

            if contact_search.get("advanced"):  # pragma: needs cover
                groups = []
                contacts = []
                query = contact_search.get("parsed_query")
                exclude = Exclusions()
            else:
                groups, contacts = ContactSearchWidget.parse_recipients(
                    self.request.org, contact_search.get("recipients", [])
                )
                query = None
                exclude = Exclusions(**contact_search.get("exclusions", {}))

            self.object = Broadcast.create(
                org,
                user,
                translations,
                base_language=base_language,
                groups=groups,
                contacts=contacts,
                query=query,
                exclude=exclude,
                optin=optin,
                template=template,
                template_variables=template_variables,
                schedule=schedule,
            )

            if send_when == ScheduleForm.SEND_NOW:
                return HttpResponseRedirect(reverse("msgs.broadcast_list"))

            return HttpResponseRedirect(self.get_success_url())

    class Update(OrgObjPermsMixin, SmartWizardUpdateView):
        form_list = [("target", TargetForm), ("compose", ComposeForm), ("schedule", ScheduleForm)]
        success_url = "@msgs.broadcast_scheduled"
        submit_button_name = _("Save")

        def get_form_kwargs(self, step):
            return {"org": self.request.org}

        def get_form_initial(self, step):
            org = self.request.org

            if step == "target":
                recipients = ContactSearchWidget.get_recipients(self.object.contacts.all(), self.object.groups.all())
                query = self.object.query if not recipients else None
                return {
                    "contact_search": {
                        "recipients": recipients,
                        "advanced": bool(query),
                        "query": query,
                        "exclusions": self.object.exclusions,
                    }
                }

            if step == "compose":
                base_language = self.object.base_language

                compose = compose_serialize(
                    self.object.translations, base_language=self.object.base_language, optin=self.object.optin
                )

                # remove any languages not present on the org
                langs = [k for k in compose.keys()]
                for iso in langs:
                    if iso != base_language and iso not in org.flow_languages:
                        del compose[iso]

                if self.object.template:
                    compose[base_language]["template"] = str(self.object.template.uuid)
                    compose[base_language]["variables"] = self.object.template_variables

                return {"compose": compose, "optin": self.object.optin, "base_language": base_language}

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
            composeBase = compose[broadcast.base_language]

            # extract our optin if it is set
            optin = composeBase.pop("optin", None)
            if optin:
                optin = OptIn.objects.filter(org=broadcast.org, uuid=optin.get("uuid")).first()

            contact_search = form_dict["target"].cleaned_data["contact_search"]

            template = composeBase.pop("template", None)
            template_variables = composeBase.pop("variables", [])
            if template:
                template = Template.objects.filter(org=broadcast.org, uuid=template).first()

            # determine our new recipients
            if contact_search.get("advanced"):  # pragma: needs cover
                groups = []
                contacts = []
                query = contact_search.get("parsed_query")
                exclusions = {}
            else:
                groups, contacts = ContactSearchWidget.parse_recipients(
                    self.request.org, contact_search.get("recipients", [])
                )
                query = None
                exclusions = contact_search.get("exclusions", {})

            broadcast.translations = compose_deserialize(compose)
            broadcast.query = query
            broadcast.exclusions = exclusions
            broadcast.optin = optin
            broadcast.template = template
            broadcast.template_variables = template_variables
            broadcast.save()

            broadcast.update_recipients(groups=groups, contacts=contacts)

            # finally, update schedule
            schedule_form = form_dict["schedule"]
            start_time = schedule_form.cleaned_data["start_datetime"]
            repeat_period = schedule_form.cleaned_data["repeat_period"]
            repeat_days_of_week = schedule_form.cleaned_data["repeat_days_of_week"]
            schedule.update_schedule(start_time, repeat_period, repeat_days_of_week=repeat_days_of_week)
            broadcast.save()

            return HttpResponseRedirect(self.get_success_url())

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

            if self.has_org_perm("msgs.broadcast_update") and obj.schedule.next_fire:
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
        permission = "msgs.broadcast_create"

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
            except mailroom.QueryValidationException as e:
                return JsonResponse({"query": "", "total": 0, "error": str(e)}, status=400)

            return JsonResponse(
                {
                    "query": query,
                    "total": total,
                    "warnings": [],
                    "blockers": self.get_blockers(self.request.org),
                }
            )

    class ToNode(NonAtomicMixin, ModalMixin, OrgPermsMixin, SmartCreateView):
        class Form(forms.ModelForm):
            text = forms.CharField(
                widget=CompletionTextarea(
                    attrs={"placeholder": _("Hi @contact.name!"), "widget_only": True, "counter": "temba-charcount"}
                )
            )

            class Meta:
                model = Broadcast
                fields = ("text",)

        permission = "msgs.broadcast_create"
        form_class = Form
        title = _("Send Message")
        success_url = "hide"
        submit_button_name = _("Send")

        blockers = {
            "no_send_channel": _(
                'To get started you need to <a href="%(link)s">add a channel</a> to your workspace which will allow '
                "you to send messages to your contacts."
            ),
        }

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["blockers"] = self.get_blockers(self.request.org)
            context["recipient_count"] = int(self.request.GET["count"])
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
            translations = {"und": {"text": form.cleaned_data["text"]}}
            node_uuid = self.request.GET["node"]

            Broadcast.create(
                self.request.org, self.request.user, translations, base_language="und", node_uuid=node_uuid
            )

            return self.render_modal_response(form)


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
                        name=_("Inbox"),
                        href=reverse("msgs.msg_inbox"),
                        count=counts[SystemLabel.TYPE_INBOX],
                        icon="inbox",
                    ),
                    self.create_menu_item(
                        menu_id="handled",
                        name=_("Handled"),
                        href=reverse("msgs.msg_flow"),
                        count=counts[SystemLabel.TYPE_FLOWS],
                        icon="flow",
                    ),
                    self.create_menu_item(
                        menu_id="archived",
                        name=_("Archived"),
                        href=reverse("msgs.msg_archived"),
                        count=counts[SystemLabel.TYPE_ARCHIVED],
                        icon="archive",
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        menu_id="outbox",
                        name=_("Outbox"),
                        href=reverse("msgs.msg_outbox"),
                        count=counts[SystemLabel.TYPE_OUTBOX] + Broadcast.get_queued(org).count(),
                    ),
                    self.create_menu_item(
                        menu_id="sent",
                        name=_("Sent"),
                        href=reverse("msgs.msg_sent"),
                        count=counts[SystemLabel.TYPE_SENT],
                    ),
                    self.create_menu_item(
                        menu_id="failed",
                        name=_("Failed"),
                        href=reverse("msgs.msg_failed"),
                        count=counts[SystemLabel.TYPE_FAILED],
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        menu_id="scheduled",
                        name=_("Scheduled"),
                        href=reverse("msgs.broadcast_scheduled"),
                        count=counts[SystemLabel.TYPE_SCHEDULED],
                    ),
                    self.create_menu_item(
                        menu_id="broadcasts",
                        name=_("Broadcasts"),
                        href=reverse("msgs.broadcast_list"),
                    ),
                    self.create_menu_item(
                        menu_id="templates",
                        name=_("Templates"),
                        href=reverse("templates.template_list"),
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        menu_id="calls",
                        name=_("Calls"),
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
        export_type = MessageExport
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

        def create_export(self, org, user, form):
            export_all = bool(int(form.cleaned_data["export_all"]))
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]
            with_fields = form.cleaned_data["with_fields"]
            with_groups = form.cleaned_data["with_groups"]

            system_label, label = (None, None) if export_all else self.derive_label()

            return MessageExport.create(
                org,
                user,
                start_date=start_date,
                end_date=end_date,
                system_label=system_label,
                label=label,
                with_fields=with_fields,
                with_groups=with_groups,
            )

    class LegacyInbox(RedirectView):
        url = "/msg"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/inbox/$" % (path)

    class Inbox(MsgListView):
        title = _("Inbox")
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
        title = _("Handled")
        template_name = "msgs/message_box.html"
        system_label = SystemLabel.TYPE_FLOWS
        bulk_actions = ("archive", "label")
        allow_export = True
        menu_path = "/msg/handled"

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact", "channel", "flow")

    class Archived(MsgListView):
        title = _("Archived")
        template_name = "msgs/msg_archived.html"
        system_label = SystemLabel.TYPE_ARCHIVED
        bulk_actions = ("restore", "label", "delete")
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact", "channel", "flow")

    class Outbox(MsgListView):
        title = _("Outbox")
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
        title = _("Sent")
        template_name = "msgs/msg_sent.html"
        system_label = SystemLabel.TYPE_SENT
        bulk_actions = ()
        allow_export = True
        default_order = ("-sent_on", "-id")

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("contact", "channel", "flow")

    class Failed(MsgListView):
        title = _("Failed")
        template_name = "msgs/msg_failed.html"
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

            if self.has_org_perm("msgs.label_delete"):
                menu.add_modax(
                    _("Delete"),
                    "delete-label",
                    reverse("msgs.label_delete", args=[self.label.uuid]),
                    title="Delete Label",
                )

            menu.new_group()

            if self.has_org_perm("msgs.msg_export"):
                menu.add_modax(_("Export"), "export-messages", self.derive_export_url(), title=_("Export Messages"))

            menu.add_modax(_("Usages"), "label-usages", reverse("msgs.label_usages", args=[self.label.uuid]))

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
    actions = ("create", "update", "usages", "delete")

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ("name", "messages")
        success_url = "uuid@msgs.msg_filter"
        form_class = LabelForm
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

    class Upload(PostOnlyMixin, OrgPermsMixin, SmartCreateView):
        """
        TODO deprecated, migrate usages to /api/v2/media.json
        """

        permission = "msgs.media_create"

        def post(self, request, *args, **kwargs):
            file = request.FILES["file"]

            filename, file_extension = os.path.splitext(file.name)
            detected_type = magic.from_buffer(next(file.chunks(chunk_size=2048)), mime=True)
            possible_extensions = mimetypes.guess_all_extensions(detected_type)
            if len(possible_extensions) > 0 and file_extension not in possible_extensions:
                return JsonResponse({"error": _("Unsupported file type")})

            if not Media.is_allowed_type(detected_type):
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
