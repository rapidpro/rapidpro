import regex
from smartmin.views import SmartCreateView, SmartCRUDL, SmartListView, SmartTemplateView, SmartUpdateView

from django import forms
from django.db.models import Min
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import get_current_timezone_name
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.contacts.models import ContactGroup, ContactURN
from temba.contacts.omnibox import omnibox_deserialize, omnibox_serialize
from temba.flows.models import Flow
from temba.formax import FormaxMixin
from temba.msgs.views import ModalMixin
from temba.orgs.views import OrgPermsMixin
from temba.schedules.models import Schedule
from temba.schedules.views import BaseScheduleForm
from temba.utils import analytics, json
from temba.utils.fields import CompletionTextarea, JSONField, OmniboxChoice, SelectWidget
from temba.utils.views import BulkActionMixin

from .models import Trigger


class BaseTriggerForm(forms.ModelForm):
    """
    Base form for creating different trigger types
    """

    flow = forms.ModelChoiceField(Flow.objects.filter(pk__lt=0), label=_("Flow"), required=True)

    def __init__(self, user, flows, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["flow"].queryset = flows.order_by("flow_type", "name")

    def clean_keyword(self):
        keyword = self.cleaned_data.get("keyword")

        if keyword is None:  # pragma: no cover
            keyword = ""

        keyword = keyword.strip()

        if keyword == "" or (keyword and not regex.match(r"^\w+$", keyword, flags=regex.UNICODE | regex.V0)):
            raise forms.ValidationError(_("Keywords must be a single word containing only letter and numbers"))

        return keyword.lower()

    def get_existing_triggers(self, cleaned_data):
        keyword = cleaned_data.get("keyword")

        if keyword is None:
            keyword = ""

        keyword = keyword.strip()
        existing = Trigger.objects.none()
        if keyword:
            existing = Trigger.objects.filter(
                org=self.user.get_org(), is_archived=False, is_active=True, keyword__iexact=keyword
            )

        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)

        return existing

    def clean(self):
        data = super().clean()
        if self.get_existing_triggers(data):
            raise forms.ValidationError(_("An active trigger already exists, triggers must be unique for each group"))
        return data

    class Meta:
        model = Trigger
        fields = ("flow",)


class DefaultTriggerForm(BaseTriggerForm):
    """
    Default trigger form which only allows selection of a non-message based flow
    """

    def __init__(self, user, *args, **kwargs):
        flows = Flow.get_triggerable_flows(user.get_org())
        super().__init__(user, flows, *args, **kwargs)


class GroupBasedTriggerForm(BaseTriggerForm):

    groups = forms.ModelMultipleChoiceField(
        queryset=ContactGroup.user_groups.filter(pk__lt=0), required=False, label=_("Only Groups")
    )

    def __init__(self, user, flows, *args, **kwargs):
        super().__init__(user, flows, *args, **kwargs)

        self.fields["groups"].queryset = ContactGroup.user_groups.filter(org=self.user.get_org(), is_active=True)
        self.fields["groups"].help_text = _(
            "Only apply this trigger to contacts in these groups. (leave empty to apply to all contacts)"
        )

    def get_existing_triggers(self, cleaned_data):
        groups = cleaned_data.get("groups", [])
        org = self.user.get_org()
        existing = Trigger.objects.filter(org=org, is_archived=False, is_active=True)

        if groups:
            existing = existing.filter(groups__in=groups)
        else:
            existing = existing.filter(groups=None)

        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)

        return existing

    class Meta(BaseTriggerForm.Meta):
        fields = ("flow", "groups")


class CatchAllTriggerForm(GroupBasedTriggerForm):
    """
    For for catchall triggers
    """

    def __init__(self, user, *args, **kwargs):
        flows = Flow.get_triggerable_flows(user.get_org())
        super().__init__(user, flows, *args, **kwargs)

    def get_existing_triggers(self, cleaned_data):
        existing = super().get_existing_triggers(cleaned_data)
        existing = existing.filter(keyword=None, trigger_type=Trigger.TYPE_CATCH_ALL)
        return existing

    class Meta(BaseTriggerForm.Meta):
        fields = ("flow", "groups")


class KeywordTriggerForm(GroupBasedTriggerForm):
    """
    Form for keyword triggers
    """

    def __init__(self, user, *args, **kwargs):
        flows = Flow.get_triggerable_flows(user.get_org())
        super().__init__(user, flows, *args, **kwargs)

    def get_existing_triggers(self, cleaned_data):
        keyword = cleaned_data.get("keyword")

        if keyword is None:
            keyword = ""

        keyword = keyword.strip()

        existing = super().get_existing_triggers(cleaned_data)
        if keyword:
            existing = existing.filter(keyword__iexact=keyword)
        return existing

    class Meta(BaseTriggerForm.Meta):
        fields = ("keyword", "match_type", "flow", "groups")


class RegisterTriggerForm(BaseTriggerForm):
    """
    Wizard form that creates keyword trigger which starts contacts in a newly created flow which adds them to a group
    """

    class AddNewGroupChoiceField(forms.ModelChoiceField):
        def clean(self, value):
            if value.startswith("[_NEW_]"):  # pragma: needs cover
                value = value[7:]

                # we must get groups for this org only
                group = ContactGroup.get_user_group_by_name(self.user.get_org(), value)
                if not group:
                    group = ContactGroup.create_static(self.user.get_org(), self.user, name=value)
                return group

            return super().clean(value)

    keyword = forms.CharField(max_length=16, required=True, help_text=_("The first word of the message text"))

    action_join_group = AddNewGroupChoiceField(
        ContactGroup.user_groups.filter(pk__lt=0),
        required=True,
        label=_("Group to Join"),
        help_text=_("The group the contact will join when they send the above keyword"),
    )

    response = forms.CharField(
        widget=CompletionTextarea(attrs={"placeholder": _("Hi @contact.name!")}),
        required=False,
        label=_("Response"),
        help_text=_("The message to send in response after they join the group (optional)"),
    )

    def __init__(self, user, *args, **kwargs):
        flows = Flow.get_triggerable_flows(user.get_org())

        super().__init__(user, flows, *args, **kwargs)

        self.fields["flow"].required = False
        group_field = self.fields["action_join_group"]
        group_field.queryset = ContactGroup.user_groups.filter(org=self.user.get_org(), is_active=True).order_by(
            "name"
        )
        group_field.user = user

    class Meta(BaseTriggerForm.Meta):
        fields = ("keyword", "action_join_group", "response", "flow")


class ScheduleTriggerForm(BaseScheduleForm, forms.ModelForm):
    repeat_period = forms.ChoiceField(choices=Schedule.REPEAT_CHOICES, label="Repeat", required=False)
    repeat_days_of_week = forms.CharField(required=False)
    start = forms.ChoiceField(choices=(("stop", "Stop Schedule"), ("later", "Schedule for later")))
    start_datetime_value = forms.IntegerField(required=False)
    flow = forms.ModelChoiceField(
        Flow.objects.filter(pk__lt=0),
        label=_("Flow"),
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Select a flow")}),
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
        flows = Flow.get_triggerable_flows(user.get_org())

        self.fields["flow"].queryset = flows

    def clean_omnibox(self):
        return omnibox_deserialize(self.user.get_org(), self.cleaned_data["omnibox"])

    class Meta:
        model = Trigger
        fields = ("flow", "omnibox", "repeat_period", "repeat_days_of_week", "start", "start_datetime_value")


class InboundCallTriggerForm(GroupBasedTriggerForm):
    def __init__(self, user, *args, **kwargs):
        flows = Flow.objects.filter(org=user.get_org(), is_active=True, is_archived=False, flow_type=Flow.TYPE_VOICE)
        super().__init__(user, flows, *args, **kwargs)

    def get_existing_triggers(self, cleaned_data):
        existing = super().get_existing_triggers(cleaned_data)
        existing = existing.filter(trigger_type=Trigger.TYPE_INBOUND_CALL)
        return existing


class NewConversationTriggerForm(BaseTriggerForm):
    """
    Form for New Conversation triggers
    """

    channel = forms.ModelChoiceField(Channel.objects.filter(pk__lt=0), label=_("Channel"), required=True)

    def __init__(self, user, *args, **kwargs):
        flows = Flow.get_triggerable_flows(user.get_org())
        super().__init__(user, flows, *args, **kwargs)

        self.fields["channel"].queryset = Channel.objects.filter(
            is_active=True,
            org=self.user.get_org(),
            schemes__overlap=list(ContactURN.SCHEMES_SUPPORTING_NEW_CONVERSATION),
        )

    def clean_channel(self):
        channel = self.cleaned_data["channel"]
        existing = Trigger.objects.filter(
            org=self.user.get_org(),
            is_active=True,
            is_archived=False,
            trigger_type=Trigger.TYPE_NEW_CONVERSATION,
            channel=channel,
        )
        if self.instance:
            existing = existing.exclude(id=self.instance.id)

        if existing.exists():
            raise forms.ValidationError(_("Trigger with this Channel already exists."))

        return self.cleaned_data["channel"]

    class Meta(BaseTriggerForm.Meta):
        fields = ("channel", "flow")


class ReferralTriggerForm(BaseTriggerForm):
    """
    Form for referral triggers
    """

    channel = forms.ModelChoiceField(
        Channel.objects.filter(pk__lt=0),
        label=_("Channel"),
        required=False,
        help_text=_("The channel to apply this trigger to, leave blank for all Facebook channels"),
    )
    referrer_id = forms.CharField(
        max_length=255, required=False, label=_("Referrer Id"), help_text=_("The referrer id that will trigger us")
    )

    def __init__(self, user, *args, **kwargs):
        flows = Flow.get_triggerable_flows(user.get_org())
        super().__init__(user, flows, *args, **kwargs)

        self.fields["channel"].queryset = Channel.objects.filter(
            is_active=True, org=self.user.get_org(), schemes__overlap=list(ContactURN.SCHEMES_SUPPORTING_REFERRALS)
        )

    def get_existing_triggers(self, cleaned_data):
        ref_id = cleaned_data.get("referrer_id", "").strip()
        channel = cleaned_data.get("channel")
        existing = Trigger.objects.filter(
            org=self.user.get_org(),
            trigger_type=Trigger.TYPE_REFERRAL,
            is_active=True,
            is_archived=False,
            referrer_id__iexact=ref_id,
        )
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)

        if channel:
            existing = existing.filter(channel=channel)

        return existing

    class Meta(BaseTriggerForm.Meta):
        fields = ("channel", "referrer_id", "flow")


class TriggerCRUDL(SmartCRUDL):
    model = Trigger
    actions = (
        "list",
        "create",
        "update",
        "archived",
        "keyword",
        "register",
        "schedule",
        "inbound_call",
        "missed_call",
        "catchall",
        "new_conversation",
        "referral",
    )

    class OrgMixin(OrgPermsMixin):
        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(*args, **kwargs)
            if not self.request.user.is_authenticated:  # pragma: needs cover
                return queryset.exclude(pk__gt=0)
            else:
                return queryset.filter(org=self.request.user.get_org())

    class Create(FormaxMixin, OrgMixin, SmartTemplateView):
        title = _("Create Trigger")

        def derive_formax_sections(self, formax, context):
            def add_section(name, url, icon):
                formax.add_section(name, reverse(url), icon=icon, action="redirect", button=_("Create Trigger"))

            org_schemes = self.org.get_schemes(Channel.ROLE_RECEIVE)
            add_section("trigger-keyword", "triggers.trigger_keyword", "icon-tree")
            add_section("trigger-register", "triggers.trigger_register", "icon-users-2")
            add_section("trigger-schedule", "triggers.trigger_schedule", "icon-clock")
            add_section("trigger-inboundcall", "triggers.trigger_inbound_call", "icon-phone2")
            add_section("trigger-missedcall", "triggers.trigger_missed_call", "icon-phone")

            if ContactURN.SCHEMES_SUPPORTING_NEW_CONVERSATION.intersection(org_schemes):
                add_section("trigger-new-conversation", "triggers.trigger_new_conversation", "icon-bubbles-2")

            if ContactURN.SCHEMES_SUPPORTING_REFERRALS.intersection(org_schemes):
                add_section("trigger-referral", "triggers.trigger_referral", "icon-exit")

            add_section("trigger-catchall", "triggers.trigger_catchall", "icon-bubble")

    class Update(ModalMixin, OrgMixin, SmartUpdateView):
        success_message = ""
        trigger_forms = {
            Trigger.TYPE_KEYWORD: KeywordTriggerForm,
            Trigger.TYPE_SCHEDULE: ScheduleTriggerForm,
            Trigger.TYPE_MISSED_CALL: DefaultTriggerForm,
            Trigger.TYPE_INBOUND_CALL: InboundCallTriggerForm,
            Trigger.TYPE_CATCH_ALL: CatchAllTriggerForm,
            Trigger.TYPE_NEW_CONVERSATION: NewConversationTriggerForm,
            Trigger.TYPE_REFERRAL: ReferralTriggerForm,
        }

        def get_form_class(self):
            trigger_type = self.object.trigger_type
            return self.trigger_forms[trigger_type]

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            if self.get_object().schedule:
                context["days"] = self.get_object().schedule.repeat_days_of_week or ""
            context["user_tz"] = get_current_timezone_name()
            context["user_tz_offset"] = int(timezone.localtime(timezone.now()).utcoffset().total_seconds() // 60)
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
                return dict(repeat_period=repeat_period, omnibox=omnibox)

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def form_valid(self, form):
            trigger = self.object
            trigger_type = trigger.trigger_type

            if trigger_type == Trigger.TYPE_SCHEDULE:
                schedule = trigger.schedule

                # update our schedule
                schedule.update_schedule(
                    form.get_start_time(schedule.org.timezone),
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

    class BaseList(OrgMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
        fields = ("name", "modified_on")
        default_template = "triggers/trigger_list.html"
        default_order = ("-modified_on",)
        search_fields = ("keyword__icontains", "flow__name__icontains", "channel__name__icontains")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["org_has_triggers"] = Trigger.objects.filter(org=self.request.user.get_org()).count()
            context["folders"] = self.get_folders()
            context["request_url"] = self.request.path
            return context

        def get_folders(self):
            org = self.request.user.get_org()
            folders = []
            folders.append(
                dict(
                    label=_("Active"),
                    url=reverse("triggers.trigger_list"),
                    count=Trigger.objects.filter(is_active=True, is_archived=False, org=org).count(),
                )
            )
            folders.append(
                dict(
                    label=_("Archived"),
                    url=reverse("triggers.trigger_archived"),
                    count=Trigger.objects.filter(is_active=True, is_archived=True, org=org).count(),
                )
            )
            return folders

    class List(BaseList):
        fields = ("keyword", "flow")
        link_fields = ("keyword", "flow")
        bulk_actions = ("archive",)
        title = _("Triggers")

        def pre_process(self, request, *args, **kwargs):
            # if they have no triggers and no search performed, send them to create page
            obj_count = super().get_queryset(*args, **kwargs).count()
            if obj_count == 0 and not request.GET.get("search", ""):
                return HttpResponseRedirect(reverse("triggers.trigger_create"))
            return super().pre_process(request, *args, **kwargs)

        def lookup_field_link(self, context, field, obj):  # pragma: needs cover
            if field == "flow" and obj.flow:
                return reverse("flows.flow_editor", args=[obj.flow.uuid])
            return super().lookup_field_link(context, field, obj)

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            qs = (
                qs.filter(is_active=True, is_archived=False)
                .annotate(earliest_group=Min("groups__name"))
                .order_by("keyword", "earliest_group")
            )
            return qs

    class Archived(BaseList):
        bulk_actions = ("restore",)
        fields = ("keyword", "flow")

        def get_queryset(self, *args, **kwargs):
            return super().get_queryset(*args, **kwargs).filter(is_active=True, is_archived=True)

    class CreateTrigger(OrgPermsMixin, SmartCreateView):
        success_url = "@triggers.trigger_list"
        success_message = ""

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

    class Keyword(CreateTrigger):
        form_class = KeywordTriggerForm

        def pre_save(self, obj, *args, **kwargs):
            obj = super().pre_save(obj, *args, **kwargs)
            obj.org = self.request.user.get_org()
            return obj

        def form_valid(self, form):
            analytics.track(self.request.user.username, "temba.trigger_created", dict(type="keyword"))
            return super().form_valid(form)

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["auto_id"] = "id_keyword_%s"
            return kwargs

    class Register(CreateTrigger):
        form_class = RegisterTriggerForm
        field_config = dict(keyword=dict(label=_("Join Keyword"), help=_("The first word of the message")))

        def form_valid(self, form):
            keyword = form.cleaned_data["keyword"]
            join_group = form.cleaned_data["action_join_group"]
            start_flow = form.cleaned_data["flow"]
            send_msg = form.cleaned_data["response"]

            org = self.request.user.get_org()
            group_flow = Flow.create_join_group(org, self.request.user, join_group, send_msg, start_flow)

            Trigger.objects.create(
                created_by=self.request.user,
                modified_by=self.request.user,
                org=self.request.user.get_org(),
                keyword=keyword,
                trigger_type=Trigger.TYPE_KEYWORD,
                flow=group_flow,
            )

            analytics.track(self.request.user.username, "temba.trigger_created", dict(type="register"))

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["auto_id"] = "id_register_%s"
            return kwargs

    class Referral(CreateTrigger):
        form_class = ReferralTriggerForm
        title = _("Create Referral Trigger")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["auto_id"] = "id_referral_%s"
            return kwargs

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()

            self.object = Trigger.create(
                org,
                user,
                Trigger.TYPE_REFERRAL,
                form.cleaned_data["flow"],
                form.cleaned_data["channel"],
                referrer_id=form.cleaned_data["referrer_id"],
            )

            analytics.track(self.request.user.username, "temba.trigger_created", dict(type="referral"))

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class Schedule(CreateTrigger):
        form_class = ScheduleTriggerForm
        title = _("Create Schedule")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["user_tz"] = get_current_timezone_name()
            context["user_tz_offset"] = int(timezone.localtime(timezone.now()).utcoffset().total_seconds() // 60)
            return context

        def form_invalid(self, form):
            if "_format" in self.request.GET and self.request.GET["_format"] == "json":  # pragma: needs cover
                return HttpResponse(
                    json.dumps(dict(status="error", errors=form.errors)), content_type="application/json", status=400
                )
            else:
                return super().form_invalid(form)

        def form_valid(self, form):
            analytics.track(self.request.user.username, "temba.trigger_created", dict(type="schedule"))
            org = self.request.user.get_org()
            start_time = form.get_start_time(org.timezone)

            schedule = Schedule.create_schedule(
                org,
                self.request.user,
                start_time,
                form.cleaned_data.get("repeat_period"),
                repeat_days_of_week=form.cleaned_data.get("repeat_days_of_week"),
            )

            recipients = self.form.cleaned_data["omnibox"]

            trigger = Trigger.objects.create(
                flow=self.form.cleaned_data["flow"],
                org=self.request.user.get_org(),
                schedule=schedule,
                trigger_type=Trigger.TYPE_SCHEDULE,
                created_by=self.request.user,
                modified_by=self.request.user,
            )

            for group in recipients["groups"]:
                trigger.groups.add(group)

            for contact in recipients["contacts"]:
                trigger.contacts.add(contact)

            self.post_save(trigger)

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["auto_id"] = "id_schedule_%s"
            return kwargs

    class InboundCall(CreateTrigger):
        form_class = InboundCallTriggerForm
        fields = ("flow", "groups")

        def pre_save(self, obj, *args, **kwargs):
            obj = super().pre_save(obj, *args, **kwargs)
            obj.org = self.request.user.get_org()
            obj.trigger_type = Trigger.TYPE_INBOUND_CALL
            return obj

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["auto_id"] = "id_inbound_call_%s"
            return kwargs

    class MissedCall(CreateTrigger):
        form_class = DefaultTriggerForm

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["auto_id"] = "id_missed_call_%s"
            return kwargs

        def form_valid(self, form):

            user = self.request.user
            org = user.get_org()

            # first archive all missed call triggers
            Trigger.objects.filter(org=org, trigger_type=Trigger.TYPE_MISSED_CALL, is_active=True).update(
                is_archived=True
            )

            # then create a new missed call trigger
            Trigger.objects.create(
                created_by=user,
                modified_by=user,
                org=org,
                trigger_type=Trigger.TYPE_MISSED_CALL,
                flow=form.cleaned_data["flow"],
            )

            analytics.track(self.request.user.username, "temba.trigger_created", dict(type="missed_call"))

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class Catchall(CreateTrigger):
        form_class = CatchAllTriggerForm

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            kwargs["auto_id"] = "id_catchall_%s"
            return kwargs

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()
            groups = form.cleaned_data["groups"]

            # first archive all catch all message triggers with matching groups
            Trigger.objects.filter(
                org=org, groups__in=groups, trigger_type=Trigger.TYPE_CATCH_ALL, is_active=True
            ).update(is_archived=True)

            # then create a new catch all trigger
            trigger = Trigger.objects.create(
                created_by=user,
                modified_by=user,
                org=org,
                trigger_type=Trigger.TYPE_CATCH_ALL,
                flow=form.cleaned_data["flow"],
            )

            # add all the groups we are relevant for
            for group in groups:
                trigger.groups.add(group)

            analytics.track(self.request.user.username, "temba.trigger_created", dict(type="catchall"))

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class NewConversation(CreateTrigger):
        form_class = NewConversationTriggerForm

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["auto_id"] = "id_new_conversation_%s"
            return kwargs

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()

            self.object = Trigger.create(
                org, user, Trigger.TYPE_NEW_CONVERSATION, form.cleaned_data["flow"], form.cleaned_data["channel"]
            )

            analytics.track(self.request.user.username, "temba.trigger_created", dict(type="new_conversation"))

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response
