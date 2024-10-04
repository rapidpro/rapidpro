import logging

from django import forms
from django.http import HttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from temba.utils.fields import CheckboxWidget, DateWidget, InputWidget, SelectMultipleWidget, SelectWidget

logger = logging.getLogger(__name__)


class ComponentFormMixin(View):
    """
    Mixin to replace form field controls with component based widgets
    """

    def customize_form_field(self, name, field):
        attrs = field.widget.attrs if field.widget.attrs else {}

        # don't replace the widget if it is already one of us
        if isinstance(
            field.widget,
            (forms.widgets.HiddenInput, CheckboxWidget, InputWidget, SelectWidget, SelectMultipleWidget, DateWidget),
        ):
            return field

        if isinstance(field.widget, (forms.widgets.Textarea,)):
            attrs["textarea"] = True
            field.widget = InputWidget(attrs=attrs)
        elif isinstance(field.widget, (forms.widgets.PasswordInput,)):  # pragma: needs cover
            attrs["password"] = True
            field.widget = InputWidget(attrs=attrs)
        elif isinstance(
            field.widget,
            (forms.widgets.TextInput, forms.widgets.EmailInput, forms.widgets.URLInput, forms.widgets.NumberInput),
        ):
            field.widget = InputWidget(attrs=attrs)
        elif isinstance(field.widget, (forms.widgets.Select,)):
            if isinstance(field, (forms.models.ModelMultipleChoiceField,)):
                field.widget = SelectMultipleWidget(attrs)  # pragma: needs cover
            else:
                field.widget = SelectWidget(attrs)

            field.widget.choices = field.choices
        elif isinstance(field.widget, (forms.widgets.CheckboxInput,)):
            field.widget = CheckboxWidget(attrs)

        return field


class ExternalURLHandler(View):
    """
    It's useful to register Courier and Mailroom URLs in RapidPro so they can be used in templates, and if they are hit
    here, we can provide the user with a error message about
    """

    service = None

    @csrf_exempt
    def dispatch(self, request, *args, **kwargs):
        logger.error(f"URL intended for {self.service} reached RapidPro", extra={"URL": request.get_full_path()})
        return HttpResponse(f"this URL should be mapped to a {self.service} instance", status=404)


class CourierURLHandler(ExternalURLHandler):
    service = "Courier"


class MailroomURLHandler(ExternalURLHandler):
    service = "Mailroom"
