import regex

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.contacts.models import ContactURN
from temba.contacts.search.omnibox import omnibox_deserialize
from temba.flows.models import Flow
from temba.schedules.views import ScheduleFormMixin
from temba.utils.fields import InputWidget, JSONField, OmniboxChoice, SelectWidget, TembaChoiceField

from .models import Trigger, TriggerType
from .views import BaseTriggerForm


class KeywordTriggerType(TriggerType):
    """
    A trigger for incoming messages that match given keywords
    """

    KEYWORD_REGEX = regex.compile(r"^\w+$", flags=regex.UNICODE | regex.V0)

    class Form(BaseTriggerForm):
        def __init__(self, user, *args, **kwargs):
            super().__init__(user, Trigger.TYPE_KEYWORD, *args, **kwargs)

        def get_conflicts_kwargs(self, cleaned_data):
            kwargs = super().get_conflicts_kwargs(cleaned_data)
            kwargs["keyword"] = cleaned_data.get("keyword") or ""
            return kwargs

        class Meta(BaseTriggerForm.Meta):
            fields = ("keyword", "match_type") + BaseTriggerForm.Meta.fields
            widgets = {"keyword": InputWidget(), "match_type": SelectWidget()}

    code = Trigger.TYPE_KEYWORD
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE)
    export_fields = TriggerType.export_fields + ("keyword",)
    required_fields = TriggerType.required_fields + ("keyword",)
    form = Form

    def validate_import_def(self, trigger_def: dict):
        super().validate_import_def(trigger_def)

        if not self.is_valid_keyword(trigger_def["keyword"]):
            raise ValueError(f"{trigger_def['keyword']} is not a valid keyword")

    def is_valid_keyword(self, keyword):
        return (
            keyword
            and len(keyword) <= Trigger.KEYWORD_MAX_LEN
            and self.KEYWORD_REGEX.match(
                keyword.strip(),
            )
        )


class CatchallTriggerType(TriggerType):
    """
    A catchall trigger for incoming messages that don't match a keyword trigger
    """

    class Form(BaseTriggerForm):
        def __init__(self, user, *args, **kwargs):
            super().__init__(user, Trigger.TYPE_CATCH_ALL, *args, **kwargs)

    code = Trigger.TYPE_CATCH_ALL
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE)
    form = Form


class ScheduledTriggerType(TriggerType):
    """
    A trigger with a time-based schedule
    """

    class Form(BaseTriggerForm, ScheduleFormMixin):
        contacts = JSONField(
            label=_("Contacts To Include"),
            required=False,
            help_text=_("Additional specific contacts to include."),
            widget=OmniboxChoice(attrs={"placeholder": _("Optional: Select contacts"), "contacts": True}),
        )

        def __init__(self, user, *args, **kwargs):
            super().__init__(user, Trigger.TYPE_SCHEDULE, *args, **kwargs)

            self.set_user(user)

        def clean_contacts(self):
            return omnibox_deserialize(self.org, self.cleaned_data["contacts"])["contacts"]

        def clean(self):
            cleaned_data = super().clean()

            # schedule triggers must use specific groups or contacts
            if not cleaned_data["groups"] and not cleaned_data["contacts"]:
                raise forms.ValidationError(_("Must provide at least one group or contact to include."))

            ScheduleFormMixin.clean(self)

            return cleaned_data

        class Meta(BaseTriggerForm.Meta):
            fields = ScheduleFormMixin.Meta.fields + BaseTriggerForm.Meta.fields + ("contacts",)

    code = Trigger.TYPE_SCHEDULE
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_BACKGROUND)
    exportable = False
    form = Form


class InboundCallTriggerType(TriggerType):
    """
    A trigger for inbound IVR calls
    """

    class Form(BaseTriggerForm):
        def __init__(self, user, *args, **kwargs):
            super().__init__(user, Trigger.TYPE_INBOUND_CALL, *args, **kwargs)

    code = Trigger.TYPE_INBOUND_CALL
    allowed_flow_types = (Flow.TYPE_VOICE,)
    form = Form


class MissedCallTriggerType(TriggerType):
    """
    A trigger for missed inbound IVR calls
    """

    class Form(BaseTriggerForm):
        def __init__(self, user, *args, **kwargs):
            super().__init__(user, Trigger.TYPE_MISSED_CALL, *args, **kwargs)

    code = Trigger.TYPE_MISSED_CALL
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE)
    form = Form


class NewConversationTriggerType(TriggerType):
    """
    A trigger for new conversations (Facebook, Telegram, Viber)
    """

    class Form(BaseTriggerForm):
        channel = TembaChoiceField(Channel.objects.none(), label=_("Channel"), required=True)

        def __init__(self, user, *args, **kwargs):
            super().__init__(user, Trigger.TYPE_NEW_CONVERSATION, *args, **kwargs)

            self.fields["channel"].queryset = self.get_channel_choices(ContactURN.SCHEMES_SUPPORTING_NEW_CONVERSATION)

        def get_conflicts_kwargs(self, cleaned_data):
            kwargs = super().get_conflicts_kwargs(cleaned_data)
            kwargs["channel"] = cleaned_data.get("channel")
            return kwargs

        class Meta(BaseTriggerForm.Meta):
            fields = ("channel",) + BaseTriggerForm.Meta.fields

    code = Trigger.TYPE_NEW_CONVERSATION
    allowed_flow_types = (Flow.TYPE_MESSAGE,)
    export_fields = TriggerType.export_fields + ("channel",)
    required_fields = TriggerType.required_fields + ("channel",)
    form = Form


class ReferralTriggerType(TriggerType):
    """
    A trigger for Facebook referral clicks
    """

    class Form(BaseTriggerForm):
        channel = TembaChoiceField(
            Channel.objects.none(),
            label=_("Channel"),
            required=False,
            help_text=_("The channel to apply this trigger to, leave blank for all Facebook channels"),
        )
        referrer_id = forms.CharField(
            max_length=255, required=False, label=_("Referrer Id"), help_text=_("The referrer id that will trigger us")
        )

        def __init__(self, user, *args, **kwargs):
            super().__init__(user, Trigger.TYPE_REFERRAL, *args, **kwargs)

            self.fields["channel"].queryset = self.get_channel_choices(ContactURN.SCHEMES_SUPPORTING_REFERRALS)

        def get_conflicts_kwargs(self, cleaned_data):
            kwargs = super().get_conflicts_kwargs(cleaned_data)
            kwargs["channel"] = cleaned_data.get("channel")
            kwargs["referrer_id"] = cleaned_data.get("referrer_id", "").strip()
            return kwargs

        class Meta(BaseTriggerForm.Meta):
            fields = ("channel", "referrer_id") + BaseTriggerForm.Meta.fields

    code = Trigger.TYPE_REFERRAL
    allowed_flow_types = (Flow.TYPE_MESSAGE,)
    export_fields = TriggerType.export_fields + ("channel",)
    form = Form


class ClosedTicketTriggerType(TriggerType):
    """
    A closed ticket trigger
    """

    class Form(BaseTriggerForm):
        def __init__(self, user, *args, **kwargs):
            super().__init__(user, Trigger.TYPE_CLOSED_TICKET, *args, **kwargs)

    code = Trigger.TYPE_CLOSED_TICKET
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_BACKGROUND)
    form = Form


TYPES = {tc.code: tc() for tc in TriggerType.__subclasses__()}
