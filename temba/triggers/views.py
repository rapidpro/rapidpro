from enum import Enum

from smartmin.views import SmartCreateView, SmartCRUDL, SmartListView, SmartTemplateView, SmartUpdateView

from django import forms
from django.db.models.functions import Upper
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.android import AndroidType
from temba.contacts.models import ContactGroup, ContactURN
from temba.contacts.omnibox import omnibox_serialize
from temba.flows.models import Flow
from temba.formax import FormaxMixin
from temba.msgs.views import ModalMixin
from temba.orgs.views import MenuMixin, OrgFilterMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.schedules.models import Schedule
from temba.utils.fields import SelectMultipleWidget, SelectWidget, TembaChoiceField, TembaMultipleChoiceField
from temba.utils.views import BulkActionMixin, ComponentFormMixin, ContentMenuMixin, SpaMixin

from .models import Trigger


class Folder(Enum):
    MESSAGES = (_("Messages"), (Trigger.TYPE_KEYWORD, Trigger.TYPE_CATCH_ALL), ("keywords__0", "-priority"))
    SCHEDULE = (_("Scheduled"), (Trigger.TYPE_SCHEDULE,), ())
    CALLS = (_("Calls"), (Trigger.TYPE_INBOUND_CALL, Trigger.TYPE_MISSED_CALL), ("-priority",))
    NEW_CONVERSATION = (
        _("New Conversation"),
        (Trigger.TYPE_NEW_CONVERSATION,),
        ("-priority",),
    )
    REFERRAL = (_("Referral"), (Trigger.TYPE_REFERRAL,), ("-priority",))
    TICKETS = (_("Tickets"), (Trigger.TYPE_CLOSED_TICKET,), ("-priority",))
    OPTINS = (_("Opt-Ins"), (Trigger.TYPE_OPT_IN, Trigger.TYPE_OPT_OUT), ("-priority",))

    def __init__(self, title, types, ordering):
        self.title = title
        self.types = types
        self.ordering = ordering

    @property
    def slug(self) -> str:
        return self.name.lower()

    def get_count(self, org) -> int:
        return org.triggers.filter(trigger_type__in=self.types, is_active=True, is_archived=False).count()

    @classmethod
    def from_slug(cls, slug: str):
        for folder in cls:
            if folder.slug == slug:
                return folder
        return None


class BaseTriggerForm(forms.ModelForm):
    """
    Base form for different trigger types.
    """

    flow = TembaChoiceField(
        Flow.objects.none(),
        label=_("Flow"),
        help_text=_("Which flow will be started."),
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Select a flow"), "searchable": True}),
    )
    groups = TembaMultipleChoiceField(
        queryset=ContactGroup.objects.none(),
        label=_("Groups To Include"),
        help_text=_("Only includes contacts in these groups."),
        required=False,
        widget=SelectMultipleWidget(
            attrs={"icons": True, "placeholder": _("Optional: Select contact groups"), "searchable": True}
        ),
    )
    exclude_groups = TembaMultipleChoiceField(
        queryset=ContactGroup.objects.none(),
        label=_("Groups To Exclude"),
        help_text=_("Excludes contacts in these groups."),
        required=False,
        widget=SelectMultipleWidget(
            attrs={"icons": True, "placeholder": _("Optional: Select contact groups"), "searchable": True}
        ),
    )

    def __init__(self, org, user, trigger_type, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org
        self.user = user
        self.trigger_type = Trigger.get_type(code=trigger_type)

        self.fields["flow"].queryset = self.get_flow_choices().order_by(Upper("name"))
        self.fields["groups"].queryset = self.get_group_choices().order_by(Upper("name"))
        self.fields["exclude_groups"].queryset = self.get_group_choices().order_by(Upper("name"))

    def get_flow_choices(self):
        return self.org.flows.filter(
            flow_type__in=self.trigger_type.allowed_flow_types, is_active=True, is_archived=False, is_system=False
        )

    def get_group_choices(self):
        return ContactGroup.get_groups(self.org)

    def get_conflicts(self, cleaned_data):
        conflicts = Trigger.get_conflicts(self.org, self.trigger_type.code, **self.get_conflicts_kwargs(cleaned_data))

        # if we're editing a trigger we can't conflict with ourselves
        if self.instance:
            conflicts = conflicts.exclude(id=self.instance.id)

        return conflicts

    def get_conflicts_kwargs(self, cleaned_data):
        return {"groups": cleaned_data.get("groups", [])}

    def clean(self):
        cleaned_data = super().clean()

        groups = cleaned_data.get("groups", [])
        exclude_groups = cleaned_data.get("exclude_groups", [])

        if set(groups).intersection(exclude_groups):
            raise forms.ValidationError(_("Can't include and exclude the same group."))

        # only check for conflicts if user is submitting valid data for all fields
        if not self.errors and self.get_conflicts(cleaned_data):
            raise forms.ValidationError(_("There already exists a trigger of this type with these options."))

        return cleaned_data

    class Meta:
        model = Trigger
        fields = ("flow", "groups", "exclude_groups")


class BaseChannelTriggerForm(BaseTriggerForm):
    """
    Base form for trigger types based on channel activity.
    """

    channel = TembaChoiceField(
        queryset=Channel.objects.none(),
        label=_("Channel"),
        help_text=_("Only include activity from this channel."),
        required=False,
        widget=SelectWidget(attrs={"placeholder": _("Optional: Select channel"), "clearable": True}),
    )

    def __init__(self, org, user, trigger_type, *args, **kwargs):
        super().__init__(org, user, trigger_type, *args, **kwargs)

        self.fields["channel"].queryset = self.get_channel_choices().order_by(Upper("name"))

    def get_channel_choices(self):
        qs = self.org.channels.filter(is_active=True)
        if self.trigger_type.allowed_channel_schemes:
            qs = qs.filter(schemes__overlap=list(self.trigger_type.allowed_channel_schemes))
        if self.trigger_type.allowed_channel_role:
            qs = qs.filter(role__contains=self.trigger_type.allowed_channel_role)
        return qs

    def get_conflicts_kwargs(self, cleaned_data):
        kwargs = super().get_conflicts_kwargs(cleaned_data)
        kwargs["channel"] = cleaned_data.get("channel")
        return kwargs

    class Meta:
        model = Trigger
        fields = ("flow", "channel", "groups", "exclude_groups")


class TriggerCRUDL(SmartCRUDL):
    model = Trigger
    actions = (
        "create",
        "create_keyword",
        "create_catchall",
        "create_schedule",
        "create_inbound_call",
        "create_missed_call",
        "create_new_conversation",
        "create_referral",
        "create_closed_ticket",
        "create_opt_in",
        "create_opt_out",
        "update",
        "list",
        "menu",
        "archived",
        "folder",
    )

    class Menu(MenuMixin, SmartTemplateView):
        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/((?P<submenu>[A-z]+)/)?$" % (path, action)

        def derive_menu(self):
            org = self.request.org
            menu = []

            org_triggers = org.triggers.filter(is_active=True)
            menu.append(
                self.create_menu_item(
                    name=_("Active"),
                    count=org_triggers.filter(is_archived=False).count(),
                    href=reverse("triggers.trigger_list"),
                    icon="trigger_active",
                )
            )

            menu.append(
                self.create_menu_item(
                    name=_("Archived"),
                    icon="trigger_archived",
                    count=org_triggers.filter(is_archived=True).count(),
                    href=reverse("triggers.trigger_archived"),
                )
            )

            menu.append(
                self.create_menu_item(name=_("New Trigger"), icon="trigger_new", href="triggers.trigger_create")
            )

            menu.append(self.create_divider())

            for folder in Folder:
                count = folder.get_count(org)
                if count:
                    menu.append(
                        self.create_menu_item(
                            name=folder.title,
                            count=count,
                            href=reverse("triggers.trigger_folder", kwargs={"folder": folder.slug}),
                        )
                    )

            return menu

    class Create(SpaMixin, FormaxMixin, OrgFilterMixin, OrgPermsMixin, SmartTemplateView):
        title = _("New Trigger")
        menu_path = "/trigger/new-trigger"

        def derive_formax_sections(self, formax, context):
            def add_section(name, url, icon):
                formax.add_section(name, reverse(url), icon=icon, action="redirect", button=_("New Trigger"))

            org_schemes = self.request.org.get_schemes(Channel.ROLE_RECEIVE)

            add_section("trigger-keyword", "triggers.trigger_create_keyword", "trigger_keyword")
            add_section("trigger-catchall", "triggers.trigger_create_catchall", "trigger_catch_all")
            add_section("trigger-schedule", "triggers.trigger_create_schedule", "trigger_schedule")
            add_section("trigger-inboundcall", "triggers.trigger_create_inbound_call", "trigger_inbound_call")

            if self.request.org.channels.filter(is_active=True, channel_type=AndroidType.code).exists():
                add_section("trigger-missedcall", "triggers.trigger_create_missed_call", "trigger_missed_call")

            if ContactURN.SCHEMES_SUPPORTING_NEW_CONVERSATION.intersection(org_schemes):
                add_section(
                    "trigger-new-conversation", "triggers.trigger_create_new_conversation", "trigger_new_conversation"
                )

            if ContactURN.SCHEMES_SUPPORTING_REFERRALS.intersection(org_schemes):
                add_section("trigger-referral", "triggers.trigger_create_referral", "trigger_referral")

            add_section("trigger-closed-ticket", "triggers.trigger_create_closed_ticket", "trigger_closed_ticket")

            if ContactURN.SCHEMES_SUPPORTING_OPTINS.intersection(org_schemes):
                add_section("trigger-opt-in", "triggers.trigger_create_opt_in", "optin")
                add_section("trigger-opt-out", "triggers.trigger_create_opt_out", "optout")

    class BaseCreate(OrgPermsMixin, ComponentFormMixin, SmartCreateView):
        trigger_type = None
        permission = "triggers.trigger_create"
        success_url = "@triggers.trigger_list"

        @property
        def type(self):
            return Trigger.get_type(code=self.trigger_type) if self.trigger_type else None

        def get_form_class(self):
            return self.form_class or self.type.form

        def get_template_names(self):
            return (f"triggers/types/{self.type.slug}/create.html",)

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            kwargs["user"] = self.request.user
            return kwargs

        def get_create_kwargs(self, user, cleaned_data):
            return {}

        def form_valid(self, form):
            user = self.request.user
            org = self.request.org
            flow = form.cleaned_data.get("flow")
            groups = form.cleaned_data["groups"]
            exclude_groups = form.cleaned_data["exclude_groups"]
            channel = form.cleaned_data.get("channel")

            create_kwargs = {"flow": flow, "groups": groups, "exclude_groups": exclude_groups, "channel": channel}
            create_kwargs.update(self.get_create_kwargs(user, form.cleaned_data))

            Trigger.create(
                org,
                user,
                self.type.code,
                **create_kwargs,
            )

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class CreateKeyword(BaseCreate):
        trigger_type = Trigger.TYPE_KEYWORD

        def get_create_kwargs(self, user, cleaned_data):
            return {"keywords": cleaned_data["keywords"], "match_type": cleaned_data["match_type"]}

    class CreateCatchall(BaseCreate):
        trigger_type = Trigger.TYPE_CATCH_ALL

    class CreateSchedule(BaseCreate):
        trigger_type = Trigger.TYPE_SCHEDULE

        def get_create_kwargs(self, user, cleaned_data):
            start_time = cleaned_data["start_datetime"]
            repeat_period = cleaned_data["repeat_period"]
            repeat_days_of_week = cleaned_data["repeat_days_of_week"]

            schedule = Schedule.create(
                self.request.org, start_time, repeat_period, repeat_days_of_week=repeat_days_of_week
            )

            return {"schedule": schedule, "contacts": cleaned_data["contacts"]}

    class CreateInboundCall(BaseCreate):
        trigger_type = Trigger.TYPE_INBOUND_CALL

        def get_create_kwargs(self, user, cleaned_data):
            return {"flow": cleaned_data.get("voice_flow") or cleaned_data.get("msg_flow")}

    class CreateMissedCall(BaseCreate):
        trigger_type = Trigger.TYPE_MISSED_CALL

    class CreateNewConversation(BaseCreate):
        trigger_type = Trigger.TYPE_NEW_CONVERSATION

    class CreateReferral(BaseCreate):
        trigger_type = Trigger.TYPE_REFERRAL

        def get_create_kwargs(self, user, cleaned_data):
            return {"referrer_id": cleaned_data["referrer_id"]}

    class CreateClosedTicket(BaseCreate):
        trigger_type = Trigger.TYPE_CLOSED_TICKET

    class CreateOptIn(BaseCreate):
        trigger_type = Trigger.TYPE_OPT_IN

    class CreateOptOut(BaseCreate):
        trigger_type = Trigger.TYPE_OPT_OUT

    class Update(ModalMixin, ComponentFormMixin, OrgObjPermsMixin, SmartUpdateView):
        def get_form_class(self):
            return self.object.type.form

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            kwargs["user"] = self.request.user
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()

            if self.object.trigger_type == Trigger.TYPE_INBOUND_CALL:
                if self.object.flow.flow_type == Flow.TYPE_VOICE:
                    initial["action"] = "answer"
                    initial["voice_flow"] = self.object.flow
                else:
                    initial["action"] = "hangup"
                    initial["msg_flow"] = self.object.flow

            elif self.object.trigger_type == Trigger.TYPE_SCHEDULE:
                schedule = self.object.schedule
                days_of_the_week = list(schedule.repeat_days_of_week) if schedule.repeat_days_of_week else []
                contacts = self.object.contacts.all()

                initial["start_datetime"] = schedule.next_fire
                initial["repeat_period"] = schedule.repeat_period
                initial["repeat_days_of_week"] = days_of_the_week
                initial["contacts"] = omnibox_serialize(self.object.org, (), contacts)
            return initial

        def form_valid(self, form):
            if self.object.trigger_type == Trigger.TYPE_INBOUND_CALL:
                voice_flow = form.cleaned_data.pop("voice_flow", None)
                msg_flow = form.cleaned_data.pop("msg_flow", None)
                self.object.flow = voice_flow or msg_flow
            elif self.object.trigger_type == Trigger.TYPE_SCHEDULE:
                self.object.schedule.update_schedule(
                    form.cleaned_data["start_datetime"],
                    form.cleaned_data["repeat_period"],
                    form.cleaned_data.get("repeat_days_of_week"),
                )

            self.object.priority = Trigger._priority(
                form.cleaned_data.get("channel"),
                form.cleaned_data.get("groups"),
                form.cleaned_data.get("exclude_groups"),
            )

            response = super().form_valid(form)
            response["REDIRECT"] = self.get_success_url()
            return response

    class BaseList(SpaMixin, OrgFilterMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
        """
        Base class for list views
        """

        permission = "triggers.trigger_list"
        fields = ("name",)
        default_template = "triggers/trigger_list.html"
        search_fields = ("keywords__icontains", "flow__name__icontains", "channel__name__icontains")

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            qs = (
                qs.filter(is_active=True)
                .order_by("-created_on")
                .select_related("flow", "channel")
                .prefetch_related("contacts", "groups", "exclude_groups")
            )
            return qs

    class List(BaseList):
        """
        Non-archived triggers of all types
        """

        bulk_actions = ("archive",)
        title = _("Active")
        menu_path = "/trigger/active"

        def pre_process(self, request, *args, **kwargs):
            # if they have no triggers and no search performed, send them to create page
            obj_count = super().get_queryset(*args, **kwargs).count()
            if obj_count == 0 and not request.GET.get("search", ""):
                return HttpResponseRedirect(reverse("triggers.trigger_create"))
            return super().pre_process(request, *args, **kwargs)

        def get_queryset(self, *args, **kwargs):
            return super().get_queryset(*args, **kwargs).filter(is_archived=False)

    class Archived(ContentMenuMixin, BaseList):
        """
        Archived triggers of all types
        """

        bulk_actions = ("restore", "delete")
        title = _("Archived")
        menu_path = "/trigger/archived"

        def build_content_menu(self, menu):
            menu.add_js("triggers_delete_all", _("Delete All"))

        def get_queryset(self, *args, **kwargs):
            return super().get_queryset(*args, **kwargs).filter(is_archived=True)

    class Folder(BaseList):
        """
        Type filtered list view
        """

        bulk_actions = ("archive",)
        paginate_by = 100

        @classmethod
        def derive_url_pattern(cls, path, action):
            return rf"^%s/%s/(?P<folder>{'|'.join([f.slug for f in Folder])}+)/$" % (path, action)

        @property
        def folder(self):
            return Folder.from_slug(slug=self.kwargs["folder"])

        def derive_menu_path(self):
            return f"/trigger/{self.folder.slug}"

        def derive_title(self):
            return self.folder.title

        def get_queryset(self, *args, **kwargs):
            return (
                super()
                .get_queryset(*args, **kwargs)
                .filter(is_archived=False, trigger_type__in=self.folder.types)
                .order_by(Trigger.type_order(), *self.folder.ordering, "-created_on")
            )
