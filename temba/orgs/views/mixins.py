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
    Mixin for views that require org permissions. `has_permission` will be called to determine if the current view is
    accessible to the current user. `has_org_perm` can be called when rendering the view to determine what other content
    (e.g. menu items) is also accessible to the user.
    """

    readonly_servicing = True

    def derive_org(self):
        return self.request.org

    def has_org_perm(self, permission: str) -> bool:
        """
        Figures out if the current user has the given permission.
        """

        org = self.derive_org()
        user = self.request.user

        # can't have an org perm without an org
        if not org:
            return False

        if user.is_anonymous:
            return False

        return user.is_staff or user.has_org_perm(org, permission)

    def has_permission(self, request, *args, **kwargs) -> bool:
        """
        Figures out if the current user has the required permission for this view.
        """

        self.kwargs = kwargs
        self.args = args
        self.request = request

        return self.has_org_perm(self.permission)

    def derive_readonly_servicing(self):
        return self.readonly_servicing

    def dispatch(self, request, *args, **kwargs):
        org = self.derive_org()
        user = self.request.user

        if org:
            # when servicing, non-superuser staff can only GET
            is_servicing = request.user.is_staff and not org.users.filter(id=request.user.id).exists()
            if (
                is_servicing
                and self.derive_readonly_servicing()
                and not request.user.is_superuser
                and request.method != "GET"
            ):
                return HttpResponseForbidden()
        else:
            if user.is_authenticated and not user.is_staff:
                return HttpResponseRedirect(reverse("orgs.org_choose"))

        return super().dispatch(request, *args, **kwargs)


class OrgObjPermsMixin(OrgPermsMixin):
    """
    Mixin for views with an object (and corresponding `get_object` method) that belongs to an org.
    """

    def get_object_org(self):
        return self.get_object().org

    def has_permission(self, request, *args, **kwargs) -> bool:
        has_perm = super().has_permission(request, *args, **kwargs)

        # even without a current org, staff can switch to the object's org
        if request.user.is_staff:
            return True

        if not has_perm:
            return False

        obj_org = self.get_object_org()
        is_this_org = self.request.org == obj_org

        return has_perm and (is_this_org or obj_org.get_users().filter(id=request.user.id).exists())

    def pre_process(self, request, *args, **kwargs):
        org = self.get_object_org()

        if self.request.org != org:
            if request.user.is_staff:
                # staff users are redirected to service page if org doesn't match
                return HttpResponseRedirect(
                    f"{reverse('staff.org_service')}?next={quote_plus(request.path)}&other_org={org.id}"
                )
            else:
                # TODO implement view to let regular users switch orgs as well
                return HttpResponseRedirect(reverse("orgs.org_choose"))

        return super().pre_process(request, *args, **kwargs)


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


class InferUserMixin:
    """
    Mixin for view whose object is the current user
    """

    @classmethod
    def derive_url_pattern(cls, path, action):
        return r"^%s/%s/$" % (path, action)

    def get_object(self, *args, **kwargs):
        return self.request.user


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
