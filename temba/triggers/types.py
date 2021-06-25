from temba.flows.models import Flow

from .models import Trigger
from .views import (
    CatchAllTriggerForm,
    ClosedTicketTriggerForm,
    InboundCallTriggerForm,
    KeywordTriggerForm,
    MissedCallTriggerForm,
    NewConversationTriggerForm,
    ReferralTriggerForm,
    ScheduleTriggerForm,
)


class TriggerType:
    code = None
    allowed_flow_types = ()
    exportable = True
    export_fields = ("trigger_type", "flow", "groups", "exclude_groups")
    form = None

    def export_def(self, trigger: Trigger) -> dict:
        all_fields = {
            "trigger_type": trigger.trigger_type,
            "flow": trigger.flow.as_export_ref(),
            "groups": [group.as_export_ref() for group in trigger.groups.all()],
            "exclude_groups": [group.as_export_ref() for group in trigger.exclude_groups.all()],
            "channel": trigger.channel.uuid if trigger.channel else None,
            "keyword": trigger.keyword,
            "match_type": trigger.match_type,
            "referrer_id": trigger.referrer_id,
        }
        return {f: all_fields[f] for f in self.export_fields}


class KeywordTriggerType(TriggerType):
    code = Trigger.TYPE_KEYWORD
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE)
    export_fields = TriggerType.export_fields + ("keyword", "match_type")
    form = KeywordTriggerForm


class CatchallTriggerType(TriggerType):
    code = Trigger.TYPE_CATCH_ALL
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE)
    form = CatchAllTriggerForm


class ScheduledTriggerType(TriggerType):
    code = Trigger.TYPE_SCHEDULE
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_BACKGROUND)
    exportable = False
    form = ScheduleTriggerForm


class InboundCallTriggerType(TriggerType):
    code = Trigger.TYPE_INBOUND_CALL
    allowed_flow_types = (Flow.TYPE_VOICE,)
    form = InboundCallTriggerForm


class MissedCallTriggerType(TriggerType):
    code = Trigger.TYPE_MISSED_CALL
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE)
    form = MissedCallTriggerForm


class NewConversationTriggerType(TriggerType):
    code = Trigger.TYPE_NEW_CONVERSATION
    allowed_flow_types = (Flow.TYPE_MESSAGE,)
    export_fields = TriggerType.export_fields + ("channel",)
    form = NewConversationTriggerForm


class ReferralTriggerType(TriggerType):
    code = Trigger.TYPE_REFERRAL
    allowed_flow_types = (Flow.TYPE_MESSAGE,)
    export_fields = TriggerType.export_fields + ("channel", "referrer_id")
    form = ReferralTriggerForm


class ClosedTicketTriggerType(TriggerType):
    code = Trigger.TYPE_CLOSED_TICKET
    allowed_flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_BACKGROUND)
    form = ClosedTicketTriggerForm


TYPES = {tc.code: tc() for tc in TriggerType.__subclasses__()}
