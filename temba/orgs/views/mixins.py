import logging
from urllib.parse import quote_plus

from django import forms
from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class OrgPermsMixin:
    """
    Get the organization and the user within the inheriting view so that it be come easy to decide
    whether this user has a certain permission for that particular organization to perform the view's actions
    """

    def get_user(self):
        return self.request.user

    def derive_org(self):
        return self.request.org

    def has_org_perm(self, permission):
        org = self.derive_org()
        if org:
            return self.get_user().has_org_perm(org, permission)
        return False

    def has_permission(self, request, *args, **kwargs):
        """
        Figures out if the current user has permissions for this view.
        """
        self.kwargs = kwargs
        self.args = args
        self.request = request

        org = self.derive_org()

        if self.get_user().is_staff and org:
            return True

        if self.get_user().is_anonymous:
            return False

        if self.get_user().has_perm(self.permission):  # pragma: needs cover
            return True

        return self.has_org_perm(self.permission)

    def dispatch(self, request, *args, **kwargs):
        # non admin authenticated users without orgs get the org chooser
        user = self.get_user()
        if user.is_authenticated and not user.is_staff:
            if not self.derive_org():
                return HttpResponseRedirect(reverse("orgs.org_choose"))

        return super().dispatch(request, *args, **kwargs)


class OrgObjPermsMixin(OrgPermsMixin):
    def get_object_org(self):
        return self.get_object().org

    def has_org_perm(self, codename):
        has_org_perm = super().has_org_perm(codename)
        if has_org_perm:
            return self.request.org == self.get_object_org()

        return False

    def has_permission(self, request, *args, **kwargs):
        user = self.request.user
        if user.is_staff:
            return True

        has_perm = super().has_permission(request, *args, **kwargs)
        if has_perm:
            return self.request.org == self.get_object_org()

    def pre_process(self, request, *args, **kwargs):
        org = self.get_object_org()
        if request.user.is_staff and self.request.org != org:
            return HttpResponseRedirect(
                f"{reverse('staff.org_service')}?next={quote_plus(request.path)}&other_org={org.id}"
            )


class RequireFeatureMixin:
    """
    Mixin for views that require that the org has a feature enabled
    """

    require_feature = None  # feature or tuple of features (requires any of them)

    def pre_process(self, request, *args, **kwargs):
        require_any = self.require_feature if isinstance(self.require_feature, tuple) else (self.require_feature,)

        if set(request.org.features).isdisjoint(require_any):
            return HttpResponseRedirect(reverse("orgs.org_workspace"))


class InferOrgMixin:
    """
    Mixin for views whose object is the current request org
    """

    @classmethod
    def derive_url_pattern(cls, path, action):
        return r"^%s/%s/$" % (path, action)

    def get_object(self, *args, **kwargs):
        return self.request.org


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
            self.fields["objects"] = forms.ModelMultipleChoiceField(queryset=queryset, required=False)
            self.fields["all"] = forms.BooleanField(required=False)
            self.fields["add"] = forms.BooleanField(required=False)

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
        user = self.request.user
        org = self.request.org
        form = BulkActionMixin.Form(
            self.get_bulk_actions(), self.get_queryset(), self.get_bulk_action_labels(), data=self.request.POST
        )

        if form.is_valid():
            action = form.cleaned_data["action"]
            objects = form.cleaned_data["objects"]
            all_objects = form.cleaned_data["all"]
            label = form.cleaned_data.get("label")

            if all_objects:
                objects = self.get_queryset()
            else:
                objects_ids = [o.id for o in objects]
                self.kwargs["bulk_action_ids"] = objects_ids  # include in kwargs so is accessible in get call below

                # convert objects queryset to one based only on org + ids
                objects = self.model._default_manager.filter(org=org, id__in=objects_ids)

            # check we have the required permission for this action
            permission = self.get_bulk_action_permission(action)
            if not user.has_perm(permission) and not user.has_org_perm(org, permission):
                return HttpResponseForbidden()

            try:
                self.apply_bulk_action(user, action, objects, label)
            except forms.ValidationError as e:
                for e in e.messages:
                    messages.info(request, e)
            except Exception:
                messages.error(request, _("An error occurred while making your changes. Please try again."))
                logger.exception(f"error applying '{action}' to {self.model.__name__} objects")

        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["actions"] = self.get_bulk_actions()
        context["labels"] = self.get_bulk_action_labels()
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


class DependencyMixin:
    dependent_order = {"campaign_event": ("relative_to__name",), "trigger": ("trigger_type", "created_on")}
    dependent_select_related = {"campaign_event": ("campaign", "relative_to")}

    def get_dependents(self, obj) -> dict:
        dependents = {}
        for type_key, type_qs in obj.get_dependents().items():
            # only include dependency types which we have at least one dependent of
            if type_qs.exists():
                type_qs = type_qs.order_by(*self.dependent_order.get(type_key, ("name",)))

                type_select_related = self.dependent_select_related.get(type_key, ())
                if type_select_related:
                    type_qs = type_qs.select_related(*type_select_related)

                dependents[type_key] = type_qs
        return dependents
