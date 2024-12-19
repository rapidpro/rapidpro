from collections import defaultdict

from ..models import OrgRole


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


def org_perms_processor(request):
    """
    Sets user_org in the context, as well as org_perms to determine org permissions.
    """

    org = None
    role = None

    if not request.user.is_anonymous:
        org = request.org

        if org:
            if request.user.is_staff:
                role = OrgRole.ADMINISTRATOR  # servicing staff get to see the UI like an org admin
            else:
                role = org.get_user_role(request.user)

    context = {"user_org": org}
    if role:
        context["org_perms"] = RolePermsWrapper(role)

    return context
