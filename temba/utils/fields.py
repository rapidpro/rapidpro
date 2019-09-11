from django import forms
from django.forms import widgets


class Select2Field(forms.Field):
    default_error_messages = {}
    widget = widgets.TextInput(attrs={"class": "select2_field", "style": "width:520px"})

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def to_python(self, value):
        return value


class CompletionTextarea(forms.Widget):
    template_name = "utils/forms/completion_textarea.haml"

    def __init__(self, attrs=None):
        default_attrs = {"width": "100%", "height": "100%"}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)
