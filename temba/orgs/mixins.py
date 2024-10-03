from urllib.parse import quote_plus

from django.http import HttpResponseRedirect
from django.urls import reverse


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
                f"{reverse('orgs.org_service')}?next={quote_plus(request.path)}&other_org={org.id}"
            )


class OrgFilterMixin:
    """
    Simple mixin to filter a view's queryset by the request org
    """

    def derive_queryset(self, *args, **kwargs):
        queryset = super().derive_queryset(*args, **kwargs)

        if not self.request.user.is_authenticated:
            return queryset.none()  # pragma: no cover
        else:
            return queryset.filter(org=self.request.org)


class InferOrgMixin:
    """
    Mixin for view whose object is the current org
    """

    @classmethod
    def derive_url_pattern(cls, path, action):
        return r"^%s/%s/$" % (path, action)

    def get_object(self, *args, **kwargs):
        return self.request.org
