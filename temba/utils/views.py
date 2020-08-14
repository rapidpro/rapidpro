import logging

from django import forms
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.utils.translation import ugettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


class PostOnlyMixin(View):
    """
    Utility mixin to make a class based view be POST only
    """

    def get(self, *args, **kwargs):
        return HttpResponse("Method Not Allowed", status=405)


class NonAtomicMixin(View):
    """
    Utility mixin to disable automatic transaction wrapping of a class based view
    """

    @transaction.non_atomic_requests
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class BulkActionMixin:
    """
    Mixin for list views which have bulk actions
    """

    bulk_actions = ()
    bulk_action_permissions = {}

    class Form(forms.Form):
        def __init__(self, actions, queryset, label_queryset, *args, **kwargs):
            super().__init__(*args, **kwargs)

            self.fields["action"] = forms.ChoiceField(choices=[(a, a) for a in actions], required=True)
            self.fields["objects"] = forms.ModelMultipleChoiceField(queryset=queryset, required=True)

            if label_queryset:
                self.fields["label"] = forms.ModelChoiceField(label_queryset, required=False)

        def clean(self):
            cleaned_data = super().clean()

            action = cleaned_data.get("action")
            label = cleaned_data.get("label")
            if action in ("label", "unlabel") and not label:
                raise forms.ValidationError("Must specify a label")

            # TODO update frontend to send back unlabel actions
            if action == "label" and self.data.get("add", "").lower() == "false":
                cleaned_data["action"] = "unlabel"

        class Meta:
            fields = ("action", "objects")

    def post(self, request, *args, **kwargs):
        """
        Handles a POSTed action form and returns the default GET response
        """
        user = self.get_user()
        org = user.get_org()
        form = BulkActionMixin.Form(
            self.get_bulk_actions(), self.get_queryset(), self.get_bulk_action_labels(), data=self.request.POST,
        )
        action_error = None

        if form.is_valid():
            action = form.cleaned_data["action"]
            objects = form.cleaned_data["objects"]
            label = form.cleaned_data.get("label")

            # convert objects queryset to one based only on org + ids
            objects = self.model._default_manager.filter(org=org, id__in=[o.id for o in objects])

            # check we have the required permission for this action
            permission = self.get_bulk_action_permission(action)
            if not user.has_perm(permission) and not user.has_org_perm(org, permission):
                return HttpResponseForbidden()

            try:
                self.apply_bulk_action(user, action, objects, label)
            except forms.ValidationError as e:
                action_error = ", ".join(e.messages)
            except Exception:
                logger.exception(f"error applying '{action}' to {self.model.__name__} objects")
                action_error = _("An error occurred while making your changes. Please try again.")

        response = self.get(request, *args, **kwargs)
        if action_error:
            response["Temba-Toast"] = action_error

        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["actions"] = self.get_bulk_actions()
        return context

    def get_bulk_actions(self):
        """
        Gets the allowed bulk actions for this view
        """
        return self.bulk_actions

    def get_bulk_action_permission(self, action):
        """
        Gets the required permission for the given action (defaults to the update permission for the model class)
        """
        default = f"{self.model._meta.app_label}.{self.model.__name__.lower()}_update"

        return self.bulk_action_permissions.get(action, default)

    def get_bulk_action_labels(self):
        """
        Views can override this to provide a set of labels for label/unlabel actions
        """
        return None

    def apply_bulk_action(self, user, action, objects, label):
        """
        Applies the given action to the given objects. If this method throws a validation error, that will become the
        error message sent back to the user.
        """
        func_name = f"apply_action_{action}"
        model_func = getattr(self.model, func_name)
        assert model_func, f"{self.model.__name__} has no method called {func_name}"

        args = [label] if label else []

        model_func(user, objects, *args)


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
