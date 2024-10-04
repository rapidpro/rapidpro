import logging

import requests

from django import forms
from django.conf import settings
from django.http import HttpResponse
from django.utils.functional import cached_property
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from temba import __version__ as temba_version
from temba.utils import json
from temba.utils.fields import CheckboxWidget, DateWidget, InputWidget, SelectMultipleWidget, SelectWidget

logger = logging.getLogger(__name__)

TEMBA_MENU_SELECTION = "temba_menu_selection"
TEMBA_CONTENT_ONLY = "x-temba-content-only"
TEMBA_VERSION = "x-temba-version"


class SpaMixin(View):
    """
    Uses SPA base template if the header is set appropriately
    """

    @cached_property
    def spa_path(self) -> tuple:
        return tuple(s for s in self.request.META.get("HTTP_TEMBA_PATH", "").split("/") if s)

    @cached_property
    def spa_referrer_path(self) -> tuple:
        return tuple(s for s in self.request.META.get("HTTP_TEMBA_REFERER_PATH", "").split("/") if s)

    def is_content_only(self):
        return "HTTP_TEMBA_SPA" in self.request.META

    def get_template_names(self):
        templates = super().get_template_names()
        spa_templates = []

        for template in templates:
            original = template.split(".")
            if len(original) == 2:
                spa_template = original[0] + "_spa." + original[1]
            if spa_template:
                spa_templates.append(spa_template)

        return spa_templates + templates

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["temba_version"] = temba_version

        if self.request.org:
            context["active_org"] = self.request.org

        if self.is_content_only():
            context["base_template"] = "spa.html"
        else:
            context["base_template"] = "frame.html"

        context["is_spa"] = True
        context["is_content_only"] = self.is_content_only()
        context["temba_path"] = self.spa_path
        context["temba_referer"] = self.spa_referrer_path
        context[TEMBA_MENU_SELECTION] = self.derive_menu_path()

        # the base page should prep the flow editor
        if not self.is_content_only():
            dev_mode = getattr(settings, "EDITOR_DEV_MODE", False)
            dev_host = getattr(settings, "EDITOR_DEV_HOST", "localhost")
            prefix = "/dev" if dev_mode else settings.STATIC_URL

            # get our list of assets to incude
            scripts = []
            styles = []

            if dev_mode:  # pragma: no cover
                response = requests.get(f"http://{dev_host}:3000/asset-manifest.json")
                data = response.json()
            else:
                with open("node_modules/@nyaruka/flow-editor/build/asset-manifest.json") as json_file:
                    data = json.load(json_file)

            for key, filename in data.get("files").items():
                # tack on our prefix for dev mode
                filename = prefix + filename

                # ignore precache manifest
                if key.startswith("precache-manifest") or key.startswith("service-worker"):
                    continue

                # css files
                if key.endswith(".css") and filename.endswith(".css"):
                    styles.append(filename)

                # javascript
                if key.endswith(".js") and filename.endswith(".js"):
                    scripts.append(filename)

            context["flow_editor_scripts"] = scripts
            context["flow_editor_styles"] = styles
            context["dev_mode"] = dev_mode

        return context

    def derive_menu_path(self):
        if hasattr(self, "menu_path"):
            return self.menu_path
        return self.request.path

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        response.headers[TEMBA_VERSION] = temba_version
        response.headers[TEMBA_MENU_SELECTION] = context[TEMBA_MENU_SELECTION]
        response.headers[TEMBA_CONTENT_ONLY] = 1 if self.is_content_only() else 0
        return response


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
