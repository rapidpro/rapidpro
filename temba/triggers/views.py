from smartmin.views import SmartCreateView, SmartCRUDL, SmartListView, SmartTemplateView, SmartUpdateView

from django import forms
from django.db.models import Min
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ngettext_lazy, ugettext_lazy as _

from temba.channels.models import Channel
from temba.contacts.models import ContactGroup, ContactURN
from temba.contacts.search.omnibox import omnibox_serialize
from temba.flows.models import Flow
from temba.formax import FormaxMixin
from temba.msgs.views import ModalMixin
from temba.orgs.views import OrgFilterMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.schedules.models import Schedule
from temba.utils.fields import (
    CompletionTextarea,
    InputWidget,
    SelectMultipleWidget,
    SelectWidget,
    TembaChoiceField,
    TembaMultipleChoiceField,
)
from temba.utils.views import BulkActionMixin, ComponentFormMixin

from .models import Trigger


class BaseTriggerForm(forms.ModelForm):
    """
    Base form for different trigger types
    """

    flow = TembaChoiceField(
        Flow.objects.none(),
        label=_("Flow"),
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Select a flow"), "searchable": True}),
    )

    groups = TembaMultipleChoiceField(
        queryset=ContactGroup.user_groups.none(),
        label=_("Groups To Include"),
        help_text=_("Only includes contacts in these groups."),
        required=False,
        widget=SelectMultipleWidget(
            attrs={"icons": True, "placeholder": _("Optional: Select contact groups"), "searchable": True}
        ),
    )
    exclude_groups = TembaMultipleChoiceField(
        queryset=ContactGroup.user_groups.none(),
        label=_("Groups To Exclude"),
        help_text=_("Excludes contacts in these groups."),
        required=False,
        widget=SelectMultipleWidget(
            attrs={"icons": True, "placeholder": _("Optional: Select contact groups"), "searchable": True}
        ),
    )

    def __init__(self, user, trigger_type, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.user = user
        self.org = user.get_org()
        self.trigger_type = Trigger.get_type(code=trigger_type)

        flow_types = self.trigger_type.allowed_flow_types
        flows = self.org.flows.filter(flow_type__in=flow_types, is_active=True, is_archived=False, is_system=False)

        self.fields["flow"].queryset = flows.order_by("name")

        groups = ContactGroup.get_user_groups(self.org, ready_only=False)

        self.fields["groups"].queryset = groups
        self.fields["exclude_groups"].queryset = groups

    def get_channel_choices(self, schemes):
        return self.org.channels.filter(is_active=True, schemes__overlap=list(schemes)).order_by("name")

    def get_conflicts(self, cleaned_data):
        conflicts = Trigger.get_conflicts(self.org, self.trigger_type.code, **self.get_conflicts_kwargs(cleaned_data))

        # if we're editing a trigger we can't conflict with ourselves
        if self.instance:
            conflicts = conflicts.exclude(id=self.instance.id)

        return conflicts

    def get_conflicts_kwargs(self, cleaned_data):
        return {"groups": cleaned_data.get("groups", [])}

    def clean_keyword(self):
        keyword = self.cleaned_data.get("keyword") or ""
        keyword = keyword.strip()

        if not self.trigger_type.is_valid_keyword(keyword):
            raise forms.ValidationError(
                _("Must be a single word containing only letters and numbers, or a single emoji character.")
            )

        return keyword.lower()

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


class RegisterTriggerForm(BaseTriggerForm):
    """
    Wizard form that creates keyword trigger which starts contacts in a newly created flow which adds them to a group
    """

    class AddNewGroupChoiceField(TembaChoiceField):
        def clean(self, value):
            if value.startswith("[_NEW_]"):  # pragma: needs cover
                value = value[7:]

                # we must get groups for this org only
                group = ContactGroup.get_user_group_by_name(self.user.get_org(), value)
                if not group:
                    group = ContactGroup.create_static(self.user.get_org(), self.user, name=value)
                return group

            return super().clean(value)

    keyword = forms.CharField(
        max_length=16,
        required=True,
        label=_("Join Keyword"),
        help_text=_("The first word of the message"),
        widget=InputWidget(),
    )

    action_join_group = AddNewGroupChoiceField(
        ContactGroup.user_groups.none(),
        required=True,
        label=_("Group to Join"),
        help_text=_("The group the contact will join when they send the above keyword"),
        widget=SelectWidget(),
    )

    response = forms.CharField(
        widget=CompletionTextarea(attrs={"placeholder": _("Hi @contact.name!")}),
        required=False,
        label=ngettext_lazy("Response", "Responses", 1),
        help_text=_("The message to send in response after they join the group (optional)"),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, Trigger.TYPE_KEYWORD, *args, **kwargs)

        # on this form flow becomes the flow to be triggered from the generated flow and is optional
        self.fields["flow"].required = False

        self.fields["action_join_group"].queryset = ContactGroup.user_groups.filter(
            org=self.org, is_active=True
        ).order_by("name")
        self.fields["action_join_group"].user = user

    def get_conflicts_kwargs(self, cleaned_data):
        kwargs = super().get_conflicts_kwargs(cleaned_data)
        kwargs["keyword"] = cleaned_data.get("keyword") or ""
        return kwargs

    class Meta(BaseTriggerForm.Meta):
        fields = ("keyword", "action_join_group", "response") + BaseTriggerForm.Meta.fields


class TriggerCRUDL(SmartCRUDL):
    model = Trigger
    actions = (
        "create",
        "create_keyword",
        "create_register",
        "create_catchall",
        "create_schedule",
        "create_inbound_call",
        "create_missed_call",
        "create_new_conversation",
        "create_referral",
        "create_closed_ticket",
        "update",
        "list",
        "archived",
        "type",
    )

    class Create(FormaxMixin, OrgFilterMixin, OrgPermsMixin, SmartTemplateView):
        title = _("Create Trigger")

        def derive_formax_sections(self, formax, context):
            def add_section(name, url, icon):
                formax.add_section(name, reverse(url), icon=icon, action="redirect", button=_("Create Trigger"))

            org_schemes = self.org.get_schemes(Channel.ROLE_RECEIVE)
            add_section("trigger-keyword", "triggers.trigger_create_keyword", "icon-tree")
            add_section("trigger-register", "triggers.trigger_create_register", "icon-users-2")
            add_section("trigger-catchall", "triggers.trigger_create_catchall", "icon-bubble")
            add_section("trigger-schedule", "triggers.trigger_create_schedule", "icon-clock")
            add_section("trigger-inboundcall", "triggers.trigger_create_inbound_call", "icon-phone2")
            add_section("trigger-missedcall", "triggers.trigger_create_missed_call", "icon-phone")

            if ContactURN.SCHEMES_SUPPORTING_NEW_CONVERSATION.intersection(org_schemes):
                add_section("trigger-new-conversation", "triggers.trigger_create_new_conversation", "icon-bubbles-2")

            if ContactURN.SCHEMES_SUPPORTING_REFERRALS.intersection(org_schemes):
                add_section("trigger-referral", "triggers.trigger_create_referral", "icon-exit")

            add_section("trigger-closed-ticket", "triggers.trigger_create_closed_ticket", "icon-ticket")

    class BaseCreate(OrgPermsMixin, ComponentFormMixin, SmartCreateView):
        trigger_type = None
        permission = "triggers.trigger_create"
        success_url = "@triggers.trigger_list"
        success_message = ""

        def get_form_class(self):
            return self.form_class or Trigger.get_type(code=self.trigger_type).form

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def get_create_kwargs(self, user, cleaned_data):
            return {}

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()
            flow = form.cleaned_data["flow"]
            groups = form.cleaned_data["groups"]
            exclude_groups = form.cleaned_data["exclude_groups"]

            Trigger.create(
                org,
                user,
                form.trigger_type.code,
                flow,
                groups=groups,
                exclude_groups=exclude_groups,
                **self.get_create_kwargs(user, form.cleaned_data),
            )

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class CreateKeyword(BaseCreate):
        trigger_type = Trigger.TYPE_KEYWORD

        def get_create_kwargs(self, user, cleaned_data):
            return {"keyword": cleaned_data["keyword"]}

    class CreateRegister(BaseCreate):
        form_class = RegisterTriggerForm

        def form_valid(self, form):
            keyword = form.cleaned_data["keyword"]
            join_group = form.cleaned_data["action_join_group"]
            start_flow = form.cleaned_data["flow"]
            send_msg = form.cleaned_data["response"]
            groups = form.cleaned_data["groups"]
            exclude_groups = form.cleaned_data["exclude_groups"]

            org = self.request.user.get_org()
            register_flow = Flow.create_join_group(org, self.request.user, join_group, send_msg, start_flow)

            Trigger.create(
                org,
                self.request.user,
                Trigger.TYPE_KEYWORD,
                register_flow,
                groups=groups,
                exclude_groups=exclude_groups,
                keyword=keyword,
            )

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class CreateCatchall(BaseCreate):
        trigger_type = Trigger.TYPE_CATCH_ALL

    class CreateSchedule(BaseCreate):
        trigger_type = Trigger.TYPE_SCHEDULE

        def get_create_kwargs(self, user, cleaned_data):
            start_time = cleaned_data["start_datetime"]
            repeat_period = cleaned_data["repeat_period"]
            repeat_days_of_week = cleaned_data["repeat_days_of_week"]

            schedule = Schedule.create_schedule(
                user.get_org(), user, start_time, repeat_period, repeat_days_of_week=repeat_days_of_week
            )

            return {"schedule": schedule, "contacts": cleaned_data["contacts"]}

    class CreateInboundCall(BaseCreate):
        trigger_type = Trigger.TYPE_INBOUND_CALL

    class CreateMissedCall(BaseCreate):
        trigger_type = Trigger.TYPE_MISSED_CALL

    class CreateNewConversation(BaseCreate):
        trigger_type = Trigger.TYPE_NEW_CONVERSATION

        def get_create_kwargs(self, user, cleaned_data):
            return {"channel": cleaned_data["channel"]}

    class CreateReferral(BaseCreate):
        trigger_type = Trigger.TYPE_REFERRAL

        def get_create_kwargs(self, user, cleaned_data):
            return {"channel": cleaned_data["channel"], "referrer_id": cleaned_data["referrer_id"]}

    class CreateClosedTicket(BaseCreate):
        trigger_type = Trigger.TYPE_CLOSED_TICKET

    class Update(ModalMixin, ComponentFormMixin, OrgObjPermsMixin, SmartUpdateView):
        success_message = ""

        def get_form_class(self):
            return self.object.type.form

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()

            if self.object.trigger_type == Trigger.TYPE_SCHEDULE:
                schedule = self.object.schedule
                days_of_the_week = list(schedule.repeat_days_of_week) if schedule.repeat_days_of_week else []
                contacts = self.object.contacts.all()

                initial["start_datetime"] = schedule.next_fire
                initial["repeat_period"] = schedule.repeat_period
                initial["repeat_days_of_week"] = days_of_the_week
                initial["contacts"] = omnibox_serialize(self.object.org, (), contacts)
            return initial

        def form_valid(self, form):
            if self.object.trigger_type == Trigger.TYPE_SCHEDULE:
                self.object.schedule.update_schedule(
                    form.cleaned_data["start_datetime"],
                    form.cleaned_data["repeat_period"],
                    form.cleaned_data.get("repeat_days_of_week"),
                )

            response = super().form_valid(form)
            response["REDIRECT"] = self.get_success_url()
            return response

    class BaseList(OrgFilterMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
        """
        Base class for list views
        """

        fields = ("name",)
        default_template = "triggers/trigger_list.html"
        search_fields = ("keyword__icontains", "flow__name__icontains", "channel__name__icontains")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org = self.request.user.get_org()
            context["main_folders"] = self.get_main_folders(org)
            context["type_folders"] = self.get_type_folders(org)
            context["request_url"] = self.request.path
            return context

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            qs = (
                qs.filter(is_active=True)
                .annotate(earliest_group=Min("groups__name"))
                .order_by("keyword", "earliest_group", "id")
                .select_related("flow", "channel")
                .prefetch_related("contacts", "groups")
            )
            return qs

        def get_main_folders(self, org):
            return [
                dict(
                    label=_("All"),
                    url=reverse("triggers.trigger_list"),
                    count=org.triggers.filter(is_active=True, is_archived=False).count(),
                ),
                dict(
                    label=_("Archived"),
                    url=reverse("triggers.trigger_archived"),
                    count=org.triggers.filter(is_active=True, is_archived=True).count(),
                ),
            ]

        def get_type_folders(self, org):
            from .types import TYPES_BY_SLUG

            org_triggers = org.triggers.filter(is_active=True, is_archived=False)
            folders = []
            for slug, trigger_type in TYPES_BY_SLUG.items():
                folders.append(
                    dict(
                        label=trigger_type.name,
                        url=reverse("triggers.trigger_type", kwargs={"type": slug}),
                        count=org_triggers.filter(trigger_type=trigger_type.code).count(),
                    )
                )
            return folders

    class List(BaseList):
        """
        Non-archived triggers of all types
        """

        bulk_actions = ("archive",)
        title = _("Triggers")

        def pre_process(self, request, *args, **kwargs):
            # if they have no triggers and no search performed, send them to create page
            obj_count = super().get_queryset(*args, **kwargs).count()
            if obj_count == 0 and not request.GET.get("search", ""):
                return HttpResponseRedirect(reverse("triggers.trigger_create"))
            return super().pre_process(request, *args, **kwargs)

        def get_queryset(self, *args, **kwargs):
            return super().get_queryset(*args, **kwargs).filter(is_archived=False)

    class Archived(BaseList):
        """
        Archived triggers of all types
        """

        bulk_actions = ("restore",)
        title = _("Archived Triggers")

        def get_queryset(self, *args, **kwargs):
            return super().get_queryset(*args, **kwargs).filter(is_archived=True)

    class Type(BaseList):
        """
        Type filtered list view
        """

        bulk_actions = ("archive",)

        @classmethod
        def derive_url_pattern(cls, path, action):
            from .types import TYPES_BY_SLUG

            return rf"^%s/%s/(?P<type>{'|'.join(TYPES_BY_SLUG.keys())}+)/$" % (path, action)

        @property
        def trigger_type(self):
            return Trigger.get_type(slug=self.kwargs["type"])

        def derive_title(self):
            return self.trigger_type.title

        def get_queryset(self, *args, **kwargs):
            return super().get_queryset(*args, **kwargs).filter(is_archived=False, trigger_type=self.trigger_type.code)
