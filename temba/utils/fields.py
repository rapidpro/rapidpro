import json
import socket
from urllib import parse

from django import forms
from django.core.validators import URLValidator
from django.forms import ValidationError
from django.utils.translation import ugettext_lazy as _


class JSONField(forms.Field):
    def to_python(self, value):
        return value


class InputWidget(forms.TextInput):
    template_name = "utils/forms/input.haml"
    is_annotated = True

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["type"] = self.input_type

        if attrs.get("hide_label", False) and context.get("label", None):  # pragma: needs cover
            del context["label"]
        return context


def validate_external_url(value):
    parsed = parse.urlparse(value)

    # if it isn't http or https, fail
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(_("%(value)s must be http or https scheme"), params={"value": value})

    # resolve the host
    try:
        host = parsed.netloc
        if parsed.port:
            host = parsed.netloc[: -(len(str(parsed.port)) + 1)]
        ip = socket.gethostbyname(host)
    except Exception:
        raise ValidationError(_("%(value)s host cannot be resolved"), params={"value": value})

    # check it isn't localhost
    if ip in ("127.0.0.1", "::1"):
        raise ValidationError(_("%(value)s cannot be localhost"), params={"value": value})


class ExternalURLField(forms.URLField):
    """
    Just like a normal URLField but also validates that the URL is external (not localhost)
    """

    default_validators = [URLValidator(), validate_external_url]


class CheckboxWidget(forms.CheckboxInput):
    template_name = "utils/forms/checkbox.haml"
    is_annotated = True


class SelectWidget(forms.Select):
    template_name = "utils/forms/select.haml"
    is_annotated = True

    def format_value(self, value):
        def format_single(v):
            if isinstance(v, (dict)):
                return v
            return str(v)

        if isinstance(value, (tuple, list)):
            return [format_single(v) for v in value]

        return [format_single(value)]

    def render(self, name, value, attrs=None, renderer=None):
        return super().render(name, value, attrs)


class SelectMultipleWidget(SelectWidget):
    template_name = "utils/forms/select.haml"
    is_annotated = True
    allow_multiple_selected = True

    def __init__(self, attrs=None):
        default_attrs = {"multi": True}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)


class ContactSearchWidget(forms.Widget):
    template_name = "utils/forms/contact_search.haml"
    is_annotated = True


class CompletionTextarea(forms.Widget):
    template_name = "utils/forms/completion_textarea.haml"
    is_annotated = True

    def __init__(self, attrs=None):
        default_attrs = {"width": "100%", "height": "100%"}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)


class OmniboxChoice(forms.Widget):
    template_name = "utils/forms/omnibox_choice.haml"
    is_annotated = True

    def __init__(self, attrs=None):
        default_attrs = {"width": "100%", "height": "100%"}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)

    def render(self, name, value, attrs=None, renderer=None):
        if value:
            value = json.dumps(value)
        return super().render(name, value, attrs)

    def value_from_datadict(self, data, files, name):
        selected = []
        for item in data.getlist(name):
            selected.append(json.loads(item))
        return selected


class ArbitraryChoiceField(forms.ChoiceField):  # pragma: needs cover
    def valid_value(self, value):
        return True


class ArbitraryJsonChoiceField(forms.ChoiceField):  # pragma: needs cover
    """
    ArbitraryChoiceField serializes names and values as json to support
    loading ajax option lists that aren't known ahead of time
    """

    def widget_attrs(self, widget):
        return {"jsonValue": True}

    def clean(self, value):
        if value is None:
            value = ""

        if isinstance(value, (str)):
            return json.loads(value)

        if isinstance(value, (tuple, list)):
            return [json.loads(_) for _ in value]

        return value

    def prepare_value(self, value):
        if value is None:
            return value

        if isinstance(value, (str)):
            return json.loads(value)

        if isinstance(value, (tuple, list)):
            if len(value) > 0:
                if isinstance(value[0], (dict)):
                    return value
                else:
                    return [json.loads(_) for _ in value]
            else:
                return value

        return json.loads(value)

    def valid_value(self, value):
        return True
