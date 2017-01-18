from __future__ import unicode_literals

from django import forms
from django.forms import widgets


class Select2Widget(widgets.TextInput):
    def render(self, name, value, attrs=None):
        return super(Select2Widget, self).render(name, value, attrs)


class Select2Field(forms.Field):
    default_error_messages = {}
    widget = Select2Widget(attrs={"class": "select2_field", "style": "width:520px"})

    def __init__(self, **kwargs):
        super(Select2Field, self).__init__(**kwargs)

    def to_python(self, value):
        return value
