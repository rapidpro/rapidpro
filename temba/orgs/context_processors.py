# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from collections import defaultdict
from .models import get_stripe_credentials


class GroupPermWrapper(object):
    def __init__(self, group):
        self.group = group
        self.empty = defaultdict(lambda: False)

        self.apps = dict()
        if self.group:
            for perm in self.group.permissions.all().select_related('content_type'):
                app_name = perm.content_type.app_label
                app_perms = self.apps.get(app_name, None)

                if not app_perms:
                    app_perms = defaultdict(lambda: False)
                    self.apps[app_name] = app_perms

                app_perms[perm.codename] = True

    def __getitem__(self, module_name):
        return self.apps.get(module_name, self.empty)

    def __iter__(self):  # pragma: needs cover
        # I am large, I contain multitudes.
        raise TypeError("GroupPermWrapper is not iterable.")

    def __contains__(self, perm_name):
        """
        Lookup by "someapp" or "someapp.someperm" in perms.
        """
        if '.' not in perm_name:  # pragma: needs cover
            return perm_name in self.apps

        else:  # pragma: needs cover
            module_name, perm_name = perm_name.split('.', 1)
            if module_name in self.apps:
                return perm_name in self.apps[module_name]
            else:
                return False


def user_group_perms_processor(request):
    """
    return context variables with org permissions to the user.
    """
    org = None
    group = None

    if hasattr(request, 'user'):
        if request.user.is_anonymous():
            group = None
        else:
            group = request.user.get_org_group()
            org = request.user.get_org()

    if group:
        context = dict(org_perms=GroupPermWrapper(group))
    else:
        context = dict()

    # make sure user_org is set on our request based on their session
    context['user_org'] = org

    return context


def settings_includer(request):
    """
    Includes a few settings that we always want in our context
    """
    return dict(STRIPE_PUBLIC_KEY=get_stripe_credentials()[0])
