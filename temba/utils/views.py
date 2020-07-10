import logging

from django import forms
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
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
    bulk_actions = ()
    bulk_action_permissions = {}

    class Form(forms.Form):
        def __init__(self, actions, queryset, *args, **kwargs):
            super().__init__(*args, **kwargs)

            self.fields["action"] = forms.ChoiceField(choices=[(a, a) for a in actions])
            self.fields["objects"] = forms.ModelMultipleChoiceField(queryset=queryset, required=True)

        class Meta:
            fields = ("action", "objects")

    def dispatch(self, *args, **kwargs):
        """
        Need to allow posts which are otherwise not allowed on list views
        """
        return super().dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        """
        Handles a POSTed action form and returns the default GET response
        """
        user = self.get_user()
        org = user.get_org()
        form = BulkActionMixin.Form(self.bulk_actions, self.get_queryset(), data=self.request.POST)

        if form.is_valid():
            action = form.cleaned_data["action"]
            objects = form.cleaned_data["objects"]

            # check we have the required permission for this action
            permission = self.get_bulk_action_permission(action)
            if not user.has_perm(permission) and not user.has_org_perm(org, permission):
                return HttpResponseForbidden()

            try:
                self.apply_bulk_action(user, action, objects)
            except Exception:
                logger.exception(f"error applying '{action}' to {self.model.__name__} objects")

                # return generic message to display to user
                return JsonResponse({"error": _("Sorry something went wrong.")}, status=400)

        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["bulk_actions"] = self.bulk_actions
        return context

    def get_bulk_action_permission(self, action):
        """
        Gets the required permission for the given action (defaults to the update permission for the model class)
        """
        default = f"{self.model._meta.app_label}.{self.model.__name__.lower()}_update"

        return self.bulk_action_permissions.get(action, default)

    def apply_bulk_action(self, user, action, objects):
        """
        Applies the given action to the given objects
        """
        func_name = f"apply_action_{action}"
        model_func = getattr(self.model, func_name)
        assert model_func, f"{self.model.__name__} has no method called {func_name}"

        model_func(user, objects)


class BaseActionForm(forms.Form):
    """
    Base form class for bulk actions against domain models, typically initiated from list views
    """

    model = None
    model_manager = "objects"
    label_model = None
    label_model_manager = "objects"
    has_is_active = False
    allowed_actions = ()

    def __init__(self, *args, **kwargs):
        org = kwargs.pop("org")
        self.user = kwargs.pop("user")

        super().__init__(*args, **kwargs)

        objects_qs = getattr(self.model, self.model_manager).filter(org=org)
        if self.has_is_active:
            objects_qs = objects_qs.filter(is_active=True)

        self.fields["action"] = forms.ChoiceField(choices=self.allowed_actions)
        self.fields["objects"] = forms.ModelMultipleChoiceField(objects_qs)
        self.fields["add"] = forms.BooleanField(required=False)
        self.fields["number"] = forms.BooleanField(required=False)

        if self.label_model:
            label_qs = getattr(self.label_model, self.label_model_manager).filter(org=org)
            self.fields["label"] = forms.ModelChoiceField(label_qs, required=False)

    def clean(self):
        data = self.cleaned_data
        action = data["action"]
        user_permissions = self.user.get_org_group().permissions

        update_perm_codename = self.model.__name__.lower() + "_update"

        update_allowed = user_permissions.filter(codename=update_perm_codename)
        delete_allowed = user_permissions.filter(codename="msg_update")
        resend_allowed = user_permissions.filter(codename="broadcast_send")

        if action in ("label", "unlabel", "archive", "restore", "block", "unblock", "unstop") and not update_allowed:
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == "delete" and not delete_allowed:  # pragma: needs cover
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == "resend" and not resend_allowed:  # pragma: needs cover
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == "label" and "label" not in self.cleaned_data:  # pragma: needs cover
            raise forms.ValidationError(_("Must specify a label"))

        if action == "unlabel" and "label" not in self.cleaned_data:  # pragma: needs cover
            raise forms.ValidationError(_("Must specify a label"))

        return data

    def execute(self):
        data = self.cleaned_data
        action = data["action"]
        objects = data["objects"]

        if action == "label":
            label = data["label"]
            add = data["add"]

            if not label:
                return dict(error=_("Missing label"))

            changed = self.model.apply_action_label(self.user, objects, label, add)
            return dict(changed=changed, added=add, label_id=label.id, label=label.name)

        elif action == "unlabel":
            label = data["label"]
            add = data["add"]

            if not label:
                return dict(error=_("Missing label"))

            changed = self.model.apply_action_label(self.user, objects, label, False)
            return dict(changed=changed, added=add, label_id=label.id, label=label.name)

        elif action == "archive":
            changed = self.model.apply_action_archive(self.user, objects)
            return dict(changed=changed)

        elif action == "block":
            changed = self.model.apply_action_block(self.user, objects)
            return dict(changed=changed)

        elif action == "unblock":
            changed = self.model.apply_action_unblock(self.user, objects)
            return dict(changed=changed)

        elif action == "restore":
            changed = self.model.apply_action_restore(self.user, objects)
            return dict(changed=changed)

        elif action == "delete":
            changed = self.model.apply_action_delete(self.user, objects)
            return dict(changed=changed)

        elif action == "unstop":
            changed = self.model.apply_action_unstop(self.user, objects)
            return dict(changed=changed)

        elif action == "resend":
            changed = self.model.apply_action_resend(self.user, objects)
            return dict(changed=changed)

        else:  # pragma: no cover
            return dict(error=_("Oops, so sorry. Something went wrong!"))


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
