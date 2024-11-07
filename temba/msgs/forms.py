from django import forms
from django.forms import Form, ValidationError
from django.utils.translation import gettext_lazy as _

from temba import mailroom
from temba.schedules.views import ScheduleFormMixin
from temba.templates.models import TemplateTranslation
from temba.utils import json, languages
from temba.utils.fields import ComposeField, ComposeWidget, ContactSearchWidget

from .models import Msg


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
