import regex

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.contacts.models import ContactURN
from temba.contacts.omnibox import omnibox_deserialize
from temba.flows.models import Flow
from temba.schedules.views import ScheduleFormMixin
from temba.utils.fields import JSONField, OmniboxChoice, SelectWidget, TembaChoiceField

from .models import ChannelTriggerType, Trigger, TriggerType
from .views import BaseChannelTriggerForm, BaseTriggerForm


class KeywordTriggerType(ChannelTriggerType):
    """
    A trigger for incoming messages that match given keywords
    """

    # keywords must a single sequence of word chars, or a single emoji (since engine treats each emoji as a word)
    KEYWORD_REGEX = regex.compile(
        r"^(\w+|[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF])$",
        flags=regex.UNICODE,
    )

    class Form(BaseChannelTriggerForm):
        keywords = forms.CharField(
            label=_("Keywords"),
            widget=SelectWidget(
                attrs={
                    "widget_only": False,
                    "multi": True,
                    "searchable": True,
                    "tags": True,
                    "space_select": True,
                    "placeholder": _("Keywords"),
                }
            ),
        )
        match_type = forms.ChoiceField(
            choices=Trigger.MATCH_TYPES,
            initial=Trigger.MATCH_FIRST_WORD,
            label=_("Trigger When"),
            help_text=_("How to match a message with a keyword."),
        )

        def __init__(self, org, user, *args, **kwargs):
            super().__init__(org, user, Trigger.TYPE_KEYWORD, *args, **kwargs)

        def clean_keywords(self):
            keywords = [k.lower() for k in self.data.getlist("keywords", [])]

            for keyword in keywords:
                if not self.trigger_type.is_valid_keyword(keyword):
                    raise forms.ValidationError(
                        _("Must be a single word containing only letters and numbers, or a single emoji character.")
                    )

            return keywords

        def get_conflicts_kwargs(self, cleaned_data):
            kwargs = super().get_conflicts_kwargs(cleaned_data)
            kwargs["keywords"] = cleaned_data["keywords"]
            return kwargs

        class Meta(BaseChannelTriggerForm.Meta):
            fields = ("keywords", "match_type") + BaseChannelTriggerForm.Meta.fields
            help_texts = {"channel": "Only include messages from this channel."}
            widgets = {"match_type": SelectWidget()}

    code = Trigger.TYPE_KEYWORD
    slug = "keyword"
    name = _("Keyword")
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE)
    allowed_channel_role = Channel.ROLE_RECEIVE
    export_fields = ChannelTriggerType.export_fields + ("keywords", "match_type")
    required_fields = ChannelTriggerType.required_fields + ("keywords",)
    form = Form

    def get_instance_name(self, trigger):
        return f"{self.name}[{', '.join(trigger.keywords)}] → {trigger.flow.name}"

    def clean_import_def(self, trigger_def: dict):
        if "keyword" in trigger_def:
            trigger_def["keywords"] = [trigger_def["keyword"]]
            del trigger_def["keyword"]

        super().clean_import_def(trigger_def)

        for keyword in trigger_def["keywords"]:
            if not self.is_valid_keyword(keyword):
                raise ValidationError(_("%(keyword)s is not a valid keyword"), params={"keyword": keyword})

    @classmethod
    def is_valid_keyword(cls, keyword: str) -> bool:
        return 0 < len(keyword) <= Trigger.KEYWORD_MAX_LEN and cls.KEYWORD_REGEX.match(keyword) is not None


class CatchallTriggerType(ChannelTriggerType):
    """
    A catchall trigger for incoming messages that don't match a keyword trigger
    """

    class Form(BaseChannelTriggerForm):
        def __init__(self, org, user, *args, **kwargs):
            super().__init__(org, user, Trigger.TYPE_CATCH_ALL, *args, **kwargs)

        class Meta(BaseChannelTriggerForm.Meta):
            help_texts = {"channel": "Only include messages from this channel."}

    code = Trigger.TYPE_CATCH_ALL
    slug = "catch_all"
    name = _("Catch All")
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE)
    allowed_channel_role = Channel.ROLE_RECEIVE
    form = Form


class ScheduledTriggerType(TriggerType):
    """
    A trigger with a time-based schedule
    """

    class Form(BaseTriggerForm, ScheduleFormMixin):
        contacts = JSONField(
            label=_("Contacts To Include"),
            required=False,
            help_text=_("Additional specific contacts that will be started in the flow."),
            widget=OmniboxChoice(attrs={"placeholder": _("Optional: Search for contacts"), "contacts": True}),
        )

        def __init__(self, org, user, *args, **kwargs):
            super().__init__(org, user, Trigger.TYPE_SCHEDULE, *args, **kwargs)

            self.set_org(org)

        def clean_contacts(self):
            groups, contacts = omnibox_deserialize(self.org, self.cleaned_data["contacts"])
            return contacts

        def clean(self):
            cleaned_data = super().clean()

            # schedule triggers must use specific groups or contacts
            if not cleaned_data["groups"] and not cleaned_data["contacts"]:
                raise forms.ValidationError(_("Must provide at least one group or contact to include."))

            ScheduleFormMixin.clean(self)

            return cleaned_data

        class Meta(BaseTriggerForm.Meta):
            fields = ScheduleFormMixin.Meta.fields + ("flow", "groups", "contacts", "exclude_groups")
            help_texts = {
                "groups": _("The groups that will be started in the flow."),
                "exclude_groups": _("Any contacts in these groups will not be started in the flow."),
            }

    code = Trigger.TYPE_SCHEDULE
    slug = "schedule"
    name = _("Schedule")
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_BACKGROUND)
    exportable = False
    form = Form


class InboundCallTriggerType(ChannelTriggerType):
    """
    A trigger for inbound IVR calls
    """

    class Form(BaseChannelTriggerForm):
        """
        Overrides the base trigger form to allow us to put voice and non-voice flow options in separate fields
        """

        ACTION_ANSWER = "answer"
        ACTION_HANGUP = "hangup"

        action = forms.ChoiceField(
            choices=(
                (ACTION_ANSWER, _("Answer call and start voice flow")),
                (ACTION_HANGUP, _("Hangup call and start messaging flow")),
            ),
            widget=SelectWidget(attrs={"widget_only": True}),
            required=True,
        )
        voice_flow = TembaChoiceField(
            Flow.objects.none(),
            required=False,
            widget=SelectWidget(attrs={"placeholder": _("Select a flow"), "searchable": True, "widget_only": True}),
        )
        msg_flow = TembaChoiceField(
            Flow.objects.none(),
            required=False,
            widget=SelectWidget(attrs={"placeholder": _("Select a flow"), "searchable": True, "widget_only": True}),
        )

        def __init__(self, org, user, *args, **kwargs):
            super().__init__(org, user, Trigger.TYPE_INBOUND_CALL, *args, **kwargs)

            flows = self.org.flows.filter(is_active=True, is_archived=False, is_system=False).order_by("name")

            del self.fields["flow"]
            self.fields["voice_flow"].queryset = flows.filter(flow_type=Flow.TYPE_VOICE)
            self.fields["msg_flow"].queryset = flows.filter(flow_type__in=(Flow.TYPE_MESSAGE, Flow.TYPE_BACKGROUND))

        def clean(self):
            cleaned_data = super().clean()

            action = cleaned_data["action"]
            voice_flow = cleaned_data.get("voice_flow")
            msg_flow = cleaned_data.get("msg_flow")
            if action == self.ACTION_ANSWER and not voice_flow:
                self.add_error("voice_flow", _("This field is required."))
            elif action == self.ACTION_HANGUP and not msg_flow:
                self.add_error("msg_flow", _("This field is required."))

            return cleaned_data

        class Meta(BaseTriggerForm.Meta):
            fields = ("action", "voice_flow", "msg_flow", "channel", "groups", "exclude_groups")
            help_texts = {"channel": "Only include calls from this channel."}

    code = Trigger.TYPE_INBOUND_CALL
    slug = "inbound_call"
    name = _("Inbound Call")
    allowed_flow_types = (Flow.TYPE_VOICE, Flow.TYPE_MESSAGE, Flow.TYPE_BACKGROUND)
    allowed_channel_role = Channel.ROLE_ANSWER
    form = Form


class MissedCallTriggerType(TriggerType):
    """
    A trigger for missed calls on Android devices
    """

    class Form(BaseTriggerForm):
        def __init__(self, org, user, *args, **kwargs):
            super().__init__(org, user, Trigger.TYPE_MISSED_CALL, *args, **kwargs)

    code = Trigger.TYPE_MISSED_CALL
    slug = "missed_call"
    name = _("Missed Call")
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_BACKGROUND)
    form = Form


class NewConversationTriggerType(ChannelTriggerType):
    """
    A trigger for new conversations (Facebook, Telegram, Viber).
    """

    class Form(BaseChannelTriggerForm):
        def __init__(self, org, user, *args, **kwargs):
            super().__init__(org, user, Trigger.TYPE_NEW_CONVERSATION, *args, **kwargs)

    code = Trigger.TYPE_NEW_CONVERSATION
    slug = "new_conversation"
    name = _("New Conversation")
    allowed_flow_types = (Flow.TYPE_MESSAGE,)
    allowed_channel_schemes = ContactURN.SCHEMES_SUPPORTING_NEW_CONVERSATION
    form = Form


class ReferralTriggerType(ChannelTriggerType):
    """
    A trigger for Facebook referral clicks
    """

    class Form(BaseChannelTriggerForm):
        referrer_id = forms.CharField(
            max_length=255, required=False, label=_("Referrer Id"), help_text=_("The referrer id that will trigger us")
        )

        def __init__(self, org, user, *args, **kwargs):
            super().__init__(org, user, Trigger.TYPE_REFERRAL, *args, **kwargs)

        def get_conflicts_kwargs(self, cleaned_data):
            kwargs = super().get_conflicts_kwargs(cleaned_data)
            kwargs["referrer_id"] = cleaned_data.get("referrer_id", "").strip()
            return kwargs

        class Meta(BaseChannelTriggerForm.Meta):
            fields = ("referrer_id",) + BaseChannelTriggerForm.Meta.fields

    code = Trigger.TYPE_REFERRAL
    slug = "referral"
    name = _("Referral")
    allowed_flow_types = (Flow.TYPE_MESSAGE,)
    allowed_channel_schemes = ContactURN.SCHEMES_SUPPORTING_REFERRALS
    form = Form


class ClosedTicketTriggerType(TriggerType):
    """
    A closed ticket trigger
    """

    class Form(BaseTriggerForm):
        def __init__(self, org, user, *args, **kwargs):
            super().__init__(org, user, Trigger.TYPE_CLOSED_TICKET, *args, **kwargs)

    code = Trigger.TYPE_CLOSED_TICKET
    slug = "closed_ticket"
    name = _("Closed Ticket")
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_BACKGROUND)
    form = Form


class OptInTriggerType(ChannelTriggerType):
    """
    An opt-in trigger type
    """

    class Form(BaseChannelTriggerForm):
        def __init__(self, org, user, *args, **kwargs):
            super().__init__(org, user, Trigger.TYPE_OPT_IN, *args, **kwargs)

    code = Trigger.TYPE_OPT_IN
    slug = "opt_in"
    name = _("Opt-In")
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_BACKGROUND)
    allowed_channel_schemes = ContactURN.SCHEMES_SUPPORTING_OPTINS
    form = Form


class OptOutTriggerType(ChannelTriggerType):
    """
    An opt-out trigger type
    """

    class Form(BaseChannelTriggerForm):
        def __init__(self, org, user, *args, **kwargs):
            super().__init__(org, user, Trigger.TYPE_OPT_OUT, *args, **kwargs)

    code = Trigger.TYPE_OPT_OUT
    slug = "opt_out"
    name = _("Opt-Out")
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_BACKGROUND)
    allowed_channel_schemes = ContactURN.SCHEMES_SUPPORTING_OPTINS
    form = Form


TYPES_BY_CODE = {}
TYPES_BY_SLUG = {}

for tt in TriggerType.__subclasses__() + ChannelTriggerType.__subclasses__():
    instance = tt()
    TYPES_BY_CODE[instance.code] = instance
    TYPES_BY_SLUG[instance.slug] = instance
