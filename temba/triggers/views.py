import regex
from smartmin.views import SmartCreateView, SmartCRUDL, SmartListView, SmartTemplateView, SmartUpdateView

from django import forms
from django.db.models import Min
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ngettext_lazy, ugettext_lazy as _

from temba.channels.models import Channel
from temba.contacts.models import ContactGroup, ContactURN
from temba.contacts.search.omnibox import omnibox_deserialize, omnibox_serialize
from temba.flows.models import Flow
from temba.formax import FormaxMixin
from temba.msgs.views import ModalMixin
from temba.orgs.views import OrgFilterMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.schedules.models import Schedule
from temba.schedules.views import BaseScheduleForm
from temba.utils import json
from temba.utils.fields import (
    CompletionTextarea,
    InputWidget,
    JSONField,
    OmniboxChoice,
    SelectMultipleWidget,
    SelectWidget,
    TembaChoiceField,
    TembaMultipleChoiceField,
)
from temba.utils.views import BulkActionMixin, ComponentFormMixin

from .models import Trigger


class BaseTriggerForm(forms.ModelForm):
    """
    Base form for different trigger types - all triggers have a flow
    """

    flow = TembaChoiceField(
        Flow.objects.none(),
        label=_("Flow"),
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Select a flow"), "searchable": True}),
    )

    conflict_message = _("There already exists a trigger of this type.")

    def __init__(self, user, trigger_type, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.org = user.get_org()
        self.trigger_type = trigger_type

        flow_types = Trigger.ALLOWED_FLOW_TYPES[self.trigger_type]
        flows = self.org.flows.filter(flow_type__in=flow_types, is_active=True, is_archived=False, is_system=False)

        self.fields["flow"].queryset = flows.order_by("name")

    def get_channel_choices(self, schemes):
        return self.org.channels.filter(is_active=True, schemes__overlap=list(schemes)).order_by("name")

    def get_conflicts(self, cleaned_data):
        conflicts = Trigger.get_conflicts(self.org, self.trigger_type, **self.get_conflicts_kwargs(cleaned_data))

        # if we're editing a trigger we can't conflict with ourselves
        if self.instance:
            conflicts = conflicts.exclude(id=self.instance.id)

        return conflicts

    def get_conflicts_kwargs(self, cleaned_data):
        return {}

    def clean_keyword(self):
        keyword = self.cleaned_data.get("keyword") or ""
        keyword = keyword.strip()

        if keyword == "" or (keyword and not regex.match(r"^\w+$", keyword, flags=regex.UNICODE | regex.V0)):
            raise forms.ValidationError(_("Must be a single word containing only letters and numbers."))

        return keyword.lower()

    def clean(self):
        cleaned_data = super().clean()

        # only check for conflicts if user is submitting valid data for all fields
        if not self.errors and self.get_conflicts(cleaned_data):
            raise forms.ValidationError(self.conflict_message)

        return cleaned_data

    class Meta:
        model = Trigger
        fields = ("flow",)


class BaseGroupsTriggerForm(BaseTriggerForm):
    """
    Base form for trigger types that support a list of inclusion groups
    """

    groups = TembaMultipleChoiceField(
        queryset=ContactGroup.user_groups.none(),
        required=False,
        widget=SelectMultipleWidget(
            attrs={
                "icons": True,
                "widget_only": True,
                "placeholder": _("Optional: Trigger only applies to these groups"),
            }
        ),
    )

    conflict_message = _("There already exists a trigger of this type for these groups.")

    def __init__(self, user, trigger_type, *args, **kwargs):
        super().__init__(user, trigger_type, *args, **kwargs)

        self.fields["groups"].queryset = ContactGroup.get_user_groups(self.org, ready_only=False)

    def get_conflicts_kwargs(self, cleaned_data):
        kwargs = super().get_conflicts_kwargs(cleaned_data)
        kwargs["groups"] = cleaned_data.get("groups", [])
        return kwargs

    class Meta(BaseTriggerForm.Meta):
        fields = ("flow", "groups")


class KeywordTriggerForm(BaseGroupsTriggerForm):
    """
    Form for keyword triggers
    """

    conflict_message = _("There already exists a trigger with this keyword for these groups.")

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, Trigger.TYPE_KEYWORD, *args, **kwargs)

    def get_conflicts_kwargs(self, cleaned_data):
        kwargs = super().get_conflicts_kwargs(cleaned_data)
        kwargs["keyword"] = cleaned_data.get("keyword") or ""
        return kwargs

    class Meta(BaseGroupsTriggerForm.Meta):
        fields = ("keyword", "match_type", "flow", "groups")
        widgets = {"keyword": InputWidget(), "match_type": SelectWidget()}


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
        max_length=16, required=True, help_text=_("The first word of the message text"), widget=InputWidget()
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
        fields = ("keyword", "action_join_group", "response", "flow")


class ScheduleTriggerForm(BaseScheduleForm, forms.ModelForm):
    repeat_period = forms.ChoiceField(
        choices=Schedule.REPEAT_CHOICES, label="Repeat", required=False, widget=SelectWidget()
    )

    repeat_days_of_week = forms.MultipleChoiceField(
        choices=Schedule.REPEAT_DAYS_CHOICES,
        label="Repeat Days",
        required=False,
        widget=SelectMultipleWidget(attrs=({"placeholder": _("Select days to repeat on")})),
    )

    start_datetime = forms.DateTimeField(
        required=False,
        label=_("Start Time"),
        widget=InputWidget(attrs={"datetimepicker": True, "placeholder": "Select a time to start the flow"}),
    )

    flow = TembaChoiceField(
        Flow.objects.none(),
        label=_("Flow"),
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Select a flow"), "searchable": True}),
        empty_label=None,
    )

    omnibox = JSONField(
        label=_("Contacts"),
        required=True,
        help_text=_("The groups and contacts the flow will be broadcast to"),
        widget=OmniboxChoice(
            attrs={"placeholder": _("Recipients, enter contacts or groups"), "groups": True, "contacts": True}
        ),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.org = user.get_org()

        flow_types = Trigger.ALLOWED_FLOW_TYPES[Trigger.TYPE_SCHEDULE]
        flows = self.org.flows.filter(flow_type__in=flow_types, is_active=True, is_archived=False, is_system=False)

        self.fields["start_datetime"].help_text = _("%s Time Zone" % self.org.timezone)
        self.fields["flow"].queryset = flows.order_by("name")

    def clean_repeat_days_of_week(self):
        return "".join(self.cleaned_data["repeat_days_of_week"])

    def clean_omnibox(self):
        return omnibox_deserialize(self.org, self.cleaned_data["omnibox"])

    class Meta:
        model = Trigger
        fields = ("flow", "omnibox", "repeat_period", "repeat_days_of_week", "start_datetime")


class InboundCallTriggerForm(BaseGroupsTriggerForm):
    """
    Form for incoming IVR call triggers
    """

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, Trigger.TYPE_INBOUND_CALL, *args, **kwargs)


class MissedCallTriggerForm(BaseTriggerForm):
    """
    Form for missed IVR call triggers
    """

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, Trigger.TYPE_MISSED_CALL, *args, **kwargs)


class NewConversationTriggerForm(BaseTriggerForm):
    """
    Form for New Conversation triggers
    """

    channel = TembaChoiceField(Channel.objects.none(), label=_("Channel"), required=True)

    conflict_message = _("There already exists a trigger of this type for this channel.")

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, Trigger.TYPE_NEW_CONVERSATION, *args, **kwargs)

        self.fields["channel"].queryset = self.get_channel_choices(ContactURN.SCHEMES_SUPPORTING_NEW_CONVERSATION)

    def get_conflicts_kwargs(self, cleaned_data):
        kwargs = super().get_conflicts_kwargs(cleaned_data)
        kwargs["channel"] = cleaned_data.get("channel")
        return kwargs

    class Meta(BaseTriggerForm.Meta):
        fields = ("channel", "flow")


class ReferralTriggerForm(BaseTriggerForm):
    """
    Form for referral triggers (Facebook)
    """

    channel = TembaChoiceField(
        Channel.objects.none(),
        label=_("Channel"),
        required=False,
        help_text=_("The channel to apply this trigger to, leave blank for all Facebook channels"),
    )
    referrer_id = forms.CharField(
        max_length=255, required=False, label=_("Referrer Id"), help_text=_("The referrer id that will trigger us")
    )

    conflict_message = _("There already exists a trigger with this referrer and channel.")

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, Trigger.TYPE_REFERRAL, *args, **kwargs)

        self.fields["channel"].queryset = self.get_channel_choices(ContactURN.SCHEMES_SUPPORTING_REFERRALS)

    def get_conflicts_kwargs(self, cleaned_data):
        kwargs = super().get_conflicts_kwargs(cleaned_data)
        kwargs["channel"] = cleaned_data.get("channel")
        kwargs["referrer_id"] = cleaned_data.get("referrer_id", "").strip()
        return kwargs

    class Meta(BaseTriggerForm.Meta):
        fields = ("channel", "referrer_id", "flow")


class CatchAllTriggerForm(BaseGroupsTriggerForm):
    """
    Form for catchall triggers (incoming messages that don't match a keyword trigger)
    """

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, Trigger.TYPE_CATCH_ALL, *args, **kwargs)


class TriggerCRUDL(SmartCRUDL):
    model = Trigger
    actions = (
        "create",
        "create_keyword",
        "create_register",
        "create_schedule",
        "create_inbound_call",
        "create_missed_call",
        "create_new_conversation",
        "create_referral",
        "create_catchall",
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
            add_section("trigger-schedule", "triggers.trigger_create_schedule", "icon-clock")
            add_section("trigger-inboundcall", "triggers.trigger_create_inbound_call", "icon-phone2")
            add_section("trigger-missedcall", "triggers.trigger_create_missed_call", "icon-phone")

            if ContactURN.SCHEMES_SUPPORTING_NEW_CONVERSATION.intersection(org_schemes):
                add_section("trigger-new-conversation", "triggers.trigger_create_new_conversation", "icon-bubbles-2")

            if ContactURN.SCHEMES_SUPPORTING_REFERRALS.intersection(org_schemes):
                add_section("trigger-referral", "triggers.trigger_create_referral", "icon-exit")

            add_section("trigger-catchall", "triggers.trigger_create_catchall", "icon-bubble")

    class BaseCreate(OrgPermsMixin, ComponentFormMixin, SmartCreateView):
        permission = "triggers.trigger_create"
        success_url = "@triggers.trigger_list"
        success_message = ""

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def form_valid(self, form):
            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class CreateKeyword(BaseCreate):
        form_class = KeywordTriggerForm

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()
            flow = form.cleaned_data["flow"]
            include_groups = form.cleaned_data["groups"]
            keyword = form.cleaned_data["keyword"]

            Trigger.create(org, user, Trigger.TYPE_KEYWORD, flow, include_groups=include_groups, keyword=keyword)

            return super().form_valid(form)

    class CreateRegister(BaseCreate):
        form_class = RegisterTriggerForm
        field_config = dict(keyword=dict(label=_("Join Keyword"), help=_("The first word of the message")))

        def form_valid(self, form):
            keyword = form.cleaned_data["keyword"]
            join_group = form.cleaned_data["action_join_group"]
            start_flow = form.cleaned_data["flow"]
            send_msg = form.cleaned_data["response"]

            org = self.request.user.get_org()
            group_flow = Flow.create_join_group(org, self.request.user, join_group, send_msg, start_flow)

            Trigger.create(org, self.request.user, Trigger.TYPE_KEYWORD, group_flow, keyword=keyword)

            return super().form_valid(form)

    class CreateSchedule(BaseCreate):
        form_class = ScheduleTriggerForm
        title = _("Create Schedule")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            return context

        def form_invalid(self, form):
            if "_format" in self.request.GET and self.request.GET["_format"] == "json":  # pragma: needs cover
                return HttpResponse(
                    json.dumps(dict(status="error", errors=form.errors)), content_type="application/json", status=400
                )
            else:
                return super().form_valid(form)

        def form_valid(self, form):
            org = self.request.user.get_org()
            start_time = form.cleaned_data["start_datetime"]

            schedule = Schedule.create_schedule(
                org,
                self.request.user,
                start_time,
                form.cleaned_data.get("repeat_period"),
                repeat_days_of_week=form.cleaned_data.get("repeat_days_of_week"),
            )

            recipients = self.form.cleaned_data["omnibox"]
            flow = self.form.cleaned_data["flow"]

            trigger = Trigger.create(
                org,
                self.request.user,
                Trigger.TYPE_SCHEDULE,
                flow,
                include_groups=recipients["groups"],
                schedule=schedule,
            )

            for contact in recipients["contacts"]:
                trigger.contacts.add(contact)

            self.post_save(trigger)

            return super().form_valid(form)

    class CreateInboundCall(BaseCreate):
        form_class = InboundCallTriggerForm
        fields = ("flow", "groups")

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()
            flow = form.cleaned_data["flow"]
            include_groups = form.cleaned_data["groups"]

            Trigger.create(org, user, Trigger.TYPE_INBOUND_CALL, flow, include_groups=include_groups)

            return super().form_valid(form)

    class CreateMissedCall(BaseCreate):
        form_class = MissedCallTriggerForm

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()
            flow = form.cleaned_data["flow"]

            Trigger.create(org, user, Trigger.TYPE_MISSED_CALL, flow)

            return super().form_valid(form)

    class CreateNewConversation(BaseCreate):
        form_class = NewConversationTriggerForm

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()
            flow = form.cleaned_data["flow"]

            Trigger.create(org, user, Trigger.TYPE_NEW_CONVERSATION, flow, channel=form.cleaned_data["channel"])

            return super().form_valid(form)

    class CreateReferral(BaseCreate):
        form_class = ReferralTriggerForm
        title = _("Create Referral Trigger")

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()
            flow = form.cleaned_data["flow"]

            Trigger.create(
                org,
                user,
                Trigger.TYPE_REFERRAL,
                flow,
                form.cleaned_data["channel"],
                referrer_id=form.cleaned_data["referrer_id"],
            )

            return super().form_valid(form)

    class CreateCatchall(BaseCreate):
        form_class = CatchAllTriggerForm

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()
            flow = form.cleaned_data["flow"]
            include_groups = form.cleaned_data["groups"]

            Trigger.create(org, user, Trigger.TYPE_CATCH_ALL, flow, include_groups=include_groups)

            return super().form_valid(form)

    class Update(ModalMixin, ComponentFormMixin, OrgObjPermsMixin, SmartUpdateView):
        success_message = ""
        trigger_forms = {
            Trigger.TYPE_KEYWORD: KeywordTriggerForm,
            Trigger.TYPE_SCHEDULE: ScheduleTriggerForm,
            Trigger.TYPE_INBOUND_CALL: InboundCallTriggerForm,
            Trigger.TYPE_MISSED_CALL: MissedCallTriggerForm,
            Trigger.TYPE_NEW_CONVERSATION: NewConversationTriggerForm,
            Trigger.TYPE_REFERRAL: ReferralTriggerForm,
            Trigger.TYPE_CATCH_ALL: CatchAllTriggerForm,
        }

        def get_form_class(self):
            trigger_type = self.object.trigger_type
            return self.trigger_forms[trigger_type]

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            if self.get_object().schedule:
                context["days"] = self.get_object().schedule.repeat_days_of_week or ""
            return context

        def form_invalid(self, form):
            if "_format" in self.request.GET and self.request.GET["_format"] == "json":  # pragma: needs cover
                return HttpResponse(
                    json.dumps(dict(status="error", errors=form.errors)), content_type="application/json", status=400
                )
            else:
                return super().form_invalid(form)

        def derive_initial(self):
            trigger = self.object
            trigger_type = trigger.trigger_type
            if trigger_type == Trigger.TYPE_SCHEDULE:
                repeat_period = trigger.schedule.repeat_period
                omnibox = omnibox_serialize(trigger.org, trigger.groups.all(), trigger.contacts.all())

                repeat_days_of_week = []
                if trigger.schedule.repeat_days_of_week:  # pragma: needs cover
                    repeat_days_of_week = list(trigger.schedule.repeat_days_of_week)

                return dict(
                    repeat_period=repeat_period,
                    omnibox=omnibox,
                    start_datetime=trigger.schedule.next_fire,
                    repeat_days_of_week=repeat_days_of_week,
                )
            return super().derive_initial()

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def form_valid(self, form):
            trigger = self.object
            trigger_type = trigger.trigger_type

            if trigger_type == Trigger.TYPE_SCHEDULE:
                schedule = trigger.schedule

                form.cleaned_data.get("repeat_days_of_week")

                # update our schedule
                schedule.update_schedule(
                    form.cleaned_data.get("start_datetime"),
                    form.cleaned_data.get("repeat_period"),
                    form.cleaned_data.get("repeat_days_of_week"),
                )

                recipients = self.form.cleaned_data["omnibox"]
                trigger.groups.clear()
                trigger.contacts.clear()

                for group in recipients["groups"]:
                    trigger.groups.add(group)

                for contact in recipients["contacts"]:
                    trigger.contacts.add(contact)

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
            folders = []
            for key, folder in Trigger.FOLDERS.items():
                folder_url = reverse("triggers.trigger_type", kwargs={"folder": key})
                folder_count = Trigger.get_folder(org, key).count()
                folders.append(dict(label=folder.label, url=folder_url, count=folder_count))
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
        Folders of related trigger types
        """

        bulk_actions = ("archive",)

        @classmethod
        def derive_url_pattern(cls, path, action):
            return rf"^%s/%s/(?P<folder>{'|'.join(Trigger.FOLDERS.keys())}+)/$" % (path, action)

        def derive_title(self):
            return Trigger.FOLDERS[self.kwargs["folder"]].title

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            return Trigger.filter_folder(qs, self.kwargs["folder"]).filter(is_archived=False)
