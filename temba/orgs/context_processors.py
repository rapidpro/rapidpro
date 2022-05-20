from collections import defaultdict

from .models import get_stripe_credentials


class GroupPermWrapper:
    """
    Provides access in templates to the permissions granted to an auth group.
    """

    def __init__(self, group):
        self.group = group
        self.empty = defaultdict(lambda: False)
        self.apps = dict()

        for perm in self.group.permissions.all().select_related("content_type"):
            app_name = perm.content_type.app_label
            app_perms = self.apps.get(app_name, None)

            if not app_perms:
                app_perms = defaultdict(lambda: False)
                self.apps[app_name] = app_perms

            app_perms[perm.codename] = True

    def __getitem__(self, module_name):
        return self.apps.get(module_name, self.empty)

    def __iter__(self):
        raise TypeError(f"{type(self)} is not iterable.")  # I am large, I contain multitudes


def user_orgs_for_brand(request):
    if request.user.is_authenticated:
        user_orgs = request.user.get_orgs(brands=request.branding.get("keys", []))
        return {"user_orgs": user_orgs}
    return {}


def user_group_perms_processor(request):
    """
    Sets user_org in the context, and org_perms if user belongs to an auth group.
    """
    context = {}

    if request.user.is_anonymous:
        org = None
        group = None
    else:
        org = request.org
        group = org.get_user_org_group(request.user) if org else None

    context["user_org"] = org
    if group:
        context["org_perms"] = GroupPermWrapper(group)

    return context


def settings_includer(request):
    """
    Includes a few settings that we always want in our context
    """
    return dict(STRIPE_PUBLIC_KEY=get_stripe_credentials()[0])
