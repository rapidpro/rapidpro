import json
import socket
from urllib import parse

from django import forms
from django.core.validators import URLValidator
from django.forms import ValidationError, widgets
from django.utils.translation import ugettext_lazy as _


class Select2Field(forms.Field):
    default_error_messages = {}
    widget = widgets.TextInput(attrs={"class": "select2_field", "style": "width:520px"})

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def to_python(self, value):
        return value


class JSONField(forms.Field):
    def to_python(self, value):
        return value


class InputWidget(forms.TextInput):
    template_name = "utils/forms/input.haml"
    is_annotated = True


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

    def render(self, name, value, attrs=None, renderer=None):
        return super().render(name, value, attrs)


class ContactSearchWidget(forms.Widget):
    template_name = "utils/forms/contact_search.haml"


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
