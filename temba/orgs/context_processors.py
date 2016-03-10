from __future__ import unicode_literals

from collections import defaultdict
from django.core.urlresolvers import reverse
from django.utils import timezone
from .models import get_stripe_credentials, UNREAD_INBOX_MSGS, UNREAD_FLOW_MSGS


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

    def __iter__(self):
        # I am large, I contain multitudes.
        raise TypeError("GroupPermWrapper is not iterable.")

    def __contains__(self, perm_name):
        """
        Lookup by "someapp" or "someapp.someperm" in perms.
        """
        if '.' not in perm_name:
            return perm_name in self.apps

        else:
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
    context = dict(STRIPE_PUBLIC_KEY=get_stripe_credentials()[0])
    return context


def unread_count_processor(request):
    """
    Context processor to calculate the number of unread messages in the inbox and on flow tabs
    """
    context = dict()
    user = request.user

    if user.is_superuser or user.is_anonymous():
        return context

    org = user.get_org()

    if org:
        # calculate and populate our unread counts on flows
        flows_unread_count = org.get_unread_msg_count(UNREAD_FLOW_MSGS)

        if request.path.find(reverse('flows.flow_list')) == 0:
            org.clear_unread_msg_count(UNREAD_FLOW_MSGS)
            org.flows_last_viewed = timezone.now()
            org.save(update_fields=['flows_last_viewed'])
            flows_unread_count = 0

        context['flows_last_viewed'] = org.flows_last_viewed
        context['flows_unread_count'] = flows_unread_count

        # calculate and populate our unread counts on inbox msgs
        msgs_unread_count = org.get_unread_msg_count(UNREAD_INBOX_MSGS)

        if request.path.find(reverse('msgs.msg_inbox')) == 0:
            org.clear_unread_msg_count(UNREAD_INBOX_MSGS)
            org.msg_last_viewed = timezone.now()
            org.save(update_fields=['msg_last_viewed'])
            msgs_unread_count = 0

        context['msgs_last_viewed'] = org.msg_last_viewed
        context['msgs_unread_count'] = msgs_unread_count

    return context
