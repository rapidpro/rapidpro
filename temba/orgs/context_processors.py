from collections import defaultdict

from .models import get_stripe_credentials


class RolePermsWrapper:
    """
    Provides access in templates to the permissions granted to an org role.
    """

    def __init__(self, role):
        self.empty = defaultdict(lambda: False)
        self.apps = defaultdict(lambda: defaultdict(lambda: False))

        for perm in role.permissions:
            (app_label, codename) = perm.split(".")

            self.apps[app_label][codename] = True

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
        role = None
    else:
        org = request.org
        role = org.get_user_role(request.user) if org else None

    context["user_org"] = org
    if role:
        context["org_perms"] = RolePermsWrapper(role)

    return context


def settings_includer(request):
    """
    Includes a few settings that we always want in our context
    """
    return dict(STRIPE_PUBLIC_KEY=get_stripe_credentials()[0])
