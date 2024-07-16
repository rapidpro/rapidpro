import ipaddress
import json
import socket
from copy import deepcopy
from datetime import datetime
from urllib.parse import urlparse
from uuid import uuid4

from django import forms
from django.core.validators import URLValidator
from django.forms import ValidationError
from django.utils.dateparse import parse_datetime
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _


@deconstructible
class UploadToIdPathAndRename(object):
    def __init__(self, path):
        self.sub_path = path

    def __call__(self, instance, filename):
        ext = filename.split(".")[-1]
        filename = "{}.{}".format(uuid4().hex, ext)
        # Use a relative path
        return "{}/{}/{}".format(self.sub_path, instance.id, filename)


class JSONField(forms.Field):
    def to_python(self, value):
        return value


class DateWidget(forms.DateTimeInput):
    template_name = "utils/forms/datepicker.html"
    is_annotated = True

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["type"] = self.input_type

        if attrs.get("hide_label", False) and context.get("label", None):  # pragma: needs cover
            del context["label"]
        return context


class TembaDateField(forms.DateField):
    widget = DateWidget()


class TembaDateTimeField(forms.DateTimeField):
    widget = DateWidget()

    def prepare_value(self, value):
        if isinstance(value, datetime):
            return str(value)
        return value

    def to_python(self, value):
        if value:
            return parse_datetime(value.strip())
        return None


class TembaWidgetMixin:
    is_annotated = True

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["type"] = self.input_type

        if attrs.get("hide_label", False) and context.get("label", None):  # pragma: needs cover
            del context["label"]
        return context


class ColorPickerWidget(TembaWidgetMixin, forms.TextInput):  # pragma: needs cover
    template_name = "utils/forms/color_picker.html"


class ImagePickerWidget(TembaWidgetMixin, forms.ClearableFileInput):
    template_name = "utils/forms/image_picker.html"


class InputWidget(TembaWidgetMixin, forms.TextInput):
    template_name = "utils/forms/input.html"
    is_annotated = True


@deconstructible
class NameValidator:
    """
    Validator for names of flows and their dependencies.
    """

    def __init__(self, max_length: int):
        self.max_length = max_length

    def __call__(self, value):
        # model forms will add their own validator based on max_length but we need this for validating for imports etc
        if len(value) > self.max_length:
            raise ValidationError(_("Cannot be longer than %(limit)d characters."), params={"limit": self.max_length})

        if value != value.strip():
            raise ValidationError(_("Cannot begin or end with whitespace."))

        for ch in '"\\':
            if ch in value:
                raise ValidationError(_("Cannot contain the character: %(char)s"), params={"char": ch})

        if "\0" in value:
            raise ValidationError(_("Cannot contain null characters."))

    def __eq__(self, other):
        return isinstance(other, NameValidator) and self.max_length == other.max_length


class ExternalURLField(forms.URLField):
    """
    Just like a normal URLField but also validates that the URL is external (not localhost)
    """

    default_validators = [URLValidator()]

    def to_python(self, value):
        """
        Overrides URLField.to_python to remove assuming http scheme
        """
        value = super(forms.CharField, self).to_python(value)
        if value:
            try:
                parsed = urlparse(value)
            except ValueError:
                raise ValidationError(self.error_messages["invalid"], code="invalid")

            if not parsed.scheme or not parsed.netloc:
                raise ValidationError(self.error_messages["invalid"], code="invalid")

            # if it isn't http or https, fail
            if parsed.scheme not in ("http", "https"):
                raise ValidationError(_("Must use HTTP or HTTPS."), params={"value": value})

            # resolve the host
            try:
                if parsed.port:
                    host = parsed.netloc[: -(len(str(parsed.port)) + 1)]
                else:
                    host = parsed.netloc

                ip = socket.gethostbyname(host)
            except Exception:
                raise ValidationError(_("Unable to resolve host."), params={"value": value})

            ip = ipaddress.ip_address(ip)

            if ip.is_loopback or ip.is_multicast or ip.is_private or ip.is_link_local:
                raise ValidationError(_("Cannot be a local or private host."), params={"value": value})

        return value


class CheckboxWidget(forms.CheckboxInput):
    template_name = "utils/forms/checkbox.html"
    is_annotated = True


class SelectWidget(forms.Select):
    template_name = "utils/forms/select.html"
    is_annotated = True
    option_inherits_attrs = False

    def __init__(self, attrs=None, choices=(), *, option_attrs={}):
        super().__init__(attrs, choices)
        self.option_attrs = option_attrs

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        if hasattr(self.choices, "option_attrs_by_value"):
            attrs = self.choices.option_attrs_by_value.get(value)

        attrs = deepcopy(attrs) if attrs else {}
        extra = self.option_attrs.get(value, {})
        attrs.update(extra)

        # django doen't include attrs if inherits is false, this doesn't seem right
        # so we'll include them ourselves
        index = str(index) if subindex is None else "%s_%s" % (index, subindex)
        option_attrs = self.build_attrs(self.attrs, attrs) if self.option_inherits_attrs else attrs

        if "id" in option_attrs:
            option_attrs["id"] = self.id_for_label(option_attrs["id"], index)

        return {
            "name": name,
            "value": value,
            "label": label,
            "selected": selected,
            "index": index,
            "attrs": option_attrs,
            "type": self.input_type,
            "template_name": self.option_template_name,
            "wrap_label": True,
        }

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
    template_name = "utils/forms/select.html"
    is_annotated = True
    allow_multiple_selected = True

    def __init__(self, attrs=None):
        default_attrs = {"multi": True}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)


class ContactSearchWidget(forms.Widget):
    template_name = "utils/forms/contact_search.html"
    is_annotated = True

    @classmethod
    def get_recipients(cls, contacts=[], groups=[]) -> list:
        recipients = []
        for contact in contacts:
            urn = contact.get_urn()
            if urn:
                urn = urn.get_display(org=contact.org, international=True)
            recipients.append({"id": contact.uuid, "name": contact.name, "urn": urn, "type": "contact"})

        for group in groups:
            recipients.append(
                {"id": group.uuid, "name": group.name, "count": group.get_member_count(), "type": "group"}
            )
        return recipients

    @classmethod
    def parse_recipients(cls, org, recipients: list) -> tuple:
        group_uuids = [r.get("id") for r in recipients if r.get("type") == "group"]
        contact_uuids = [r.get("id") for r in recipients if r.get("type") == "contact"]
        return (
            org.groups.filter(uuid__in=group_uuids),
            org.contacts.filter(uuid__in=contact_uuids),
        )

    def render(self, name, value, attrs=None, renderer=None):
        if value:
            value = json.loads(value)
            attrs = attrs or {}
            if value:
                attrs["advanced"] = value["advanced"]
                attrs["query"] = value.get("query", None)
                attrs["recipients"] = json.dumps(value.get("recipients", []))
                attrs["exclusions"] = json.dumps(value.get("exclusions", []))

        return super().render(name, value, attrs)


class CompletionTextarea(forms.Widget):
    template_name = "utils/forms/completion_textarea.html"
    is_annotated = True

    def __init__(self, attrs=None):
        default_attrs = {"width": "100%", "height": "100%"}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)


class OmniboxChoice(forms.Widget):
    template_name = "utils/forms/omnibox_choice.html"
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


class ComposeWidget(forms.Widget):
    template_name = "utils/forms/compose.html"
    is_annotated = True

    def render(self, name, value, attrs=None, renderer=None):
        render_value = json.dumps(value)
        return super().render(name, render_value, attrs)

    def value_from_datadict(self, data, files, name):
        return json.loads(data[name])


class ComposeField(JSONField):
    widget = ComposeWidget


class TembaChoiceIterator(forms.models.ModelChoiceIterator):
    def __init__(self, field):
        super().__init__(field)
        self.option_attrs_by_value = dict()

    def choice(self, obj):
        value = self.field.prepare_value(obj)
        option = (value, self.field.label_from_instance(obj))
        if hasattr(obj, "get_attrs"):
            self.option_attrs_by_value[value] = obj.get_attrs()

        return option


class TembaChoiceField(forms.ModelChoiceField):
    iterator = TembaChoiceIterator
    widget = SelectWidget()


class TembaMultipleChoiceField(forms.ModelMultipleChoiceField):
    iterator = TembaChoiceIterator
    widget = SelectMultipleWidget()


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
        super().validate(value)

        if isinstance(value, str):
            return json.loads(value)

        if isinstance(value, (tuple, list)):
            return [json.loads(_) for _ in value]

        return value

    def prepare_value(self, value):
        if value is None:
            return value

        if isinstance(value, str):
            return json.loads(value)

        if isinstance(value, (tuple, list)):
            if len(value) > 0:
                if isinstance(value[0], dict):
                    return value
                else:
                    return [json.loads(_) for _ in value]
            else:
                return value

        return json.loads(value)

    def valid_value(self, value):
        return True
