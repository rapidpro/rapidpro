from collections import defaultdict

from django.utils.translation import ugettext_lazy as _

from .models import get_stripe_credentials


class GroupPermWrapper(object):
    def __init__(self, group):
        self.group = group
        self.empty = defaultdict(lambda: False)

        self.apps = dict()
        if self.group:
            for perm in self.group.permissions.all().select_related("content_type"):
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
        if "." not in perm_name:  # pragma: needs cover
            return perm_name in self.apps

        else:  # pragma: needs cover
            module_name, perm_name = perm_name.split(".", 1)
            if module_name in self.apps:
                return perm_name in self.apps[module_name]
            else:
                return False


def user_orgs_for_brand(request):
    if hasattr(request, "user"):
        if not request.user.is_anonymous:
            user_orgs = request.user.get_user_orgs(request.branding.get("keys", []))
            return dict(user_orgs=user_orgs)
    return {}


def user_group_perms_processor(request):
    """
    return context variables with org permissions to the user.
    """
    org = None
    group = None
    org_perms = []

    if hasattr(request, "user"):
        if request.user.is_anonymous:
            group = None
        else:
            group = request.user.get_org_group()
            org = request.user.get_org()

    if group:
        org_perms = GroupPermWrapper(group)
        context = dict(org_perms=org_perms)
    else:
        context = dict()

    # make sure user_org is set on our request based on their session
    context["user_org"] = org

    return context


def nav_processor(request):  # pragma: needs cover
    context = dict()
    org_perms = []
    if hasattr(request, "user"):
        if not request.user.is_anonymous:
            group = request.user.get_org_group()
            org_perms = GroupPermWrapper(group)

    # construct our navigation options
    nav, nav_overflow = get_nav(request, org_perms)
    context["nav"] = nav[0:5]
    context["nav_overflow"] = nav[5:] + nav_overflow
    return context


def get_nav(request, org_perms):  # pragma: needs cover
    nav = []
    nav_overflow = []

    has_outgoing_channel = False
    org = None
    if not request.user.is_anonymous:
        org = request.user.get_org()
        if org:
            send_channel = org.get_send_channel()
            call_channel = org.get_call_channel()
            has_outgoing_channel = send_channel or call_channel

    is_superuser = request.user.is_superuser

    if not is_superuser:
        if "msgs.msg_inbox" in org_perms:
            nav.append(
                dict(
                    title=_("Messages"),
                    href="msgs.msg_inbox",
                    icon="bubble",
                    active="inbox|outbox|broadcast|call|msg/filter|msg/flow|msg/archived|failed",
                )
            )

        if "contacts.contact_list" in org_perms:
            nav.append(
                dict(title=_("Contacts"), href="contacts.contact_list", icon="contact", active="contact|imports")
            )

        if "flows.flow_list" in org_perms:
            nav.append(dict(title=_("Flows"), href="flows.flow_list", icon="flow", active="(?<!msg)/flow"))

        if "campaigns.campaign_list" in org_perms:
            nav.append(dict(title=_("Campaigns"), href="campaigns.campaign_list", icon="campaign", active="campaign"))

        if "triggers.trigger_list" in org_perms:
            nav.append(dict(title=_("Triggers"), href="triggers.trigger_list", icon="trigger", active="trigger"))

    if "channels.channel_list" in org_perms or request.user.is_superuser:
        if not has_outgoing_channel and "channels.channel_claim" in org_perms:
            nav.append(dict(title=_("Channels"), href="channels.channel_list", tag="channels", active="channels"))

    if not org:
        if is_superuser or request.user.has_perm("auth.user_list"):
            nav.append(dict(title=_("Users"), href="orgs.user_list", tag="groups", active="users"))

        if is_superuser or request.user.has_perm("orgs.org_manage"):
            nav.append(dict(title=_("Orgs"), href="orgs.org_manage", tag="orgs", active="org"))

    if is_superuser or request.user.has_perm("orgs.org_dashboard"):
        nav.append(
            dict(title=_("Dashboard"), href="dashboard.dashboard_home", tag="dashboard", active="dashboard(?!/flows)")
        )

    if is_superuser or request.user.has_perm("apks.apk_list"):
        nav.append(dict(title=_("Android"), href="apks.apk_list", tag="android", active="apks"))

    if request.user.is_anonymous:
        nav.append(dict(title=_("Sign in"), href="users.user_check_login", tag="login", active="login"))
    else:
        nav_overflow.append(dict(title=_("Sign out"), href="users.user_logout", tag="logout", active="logout"))

    return nav, nav_overflow


def settings_includer(request):
    """
    Includes a few settings that we always want in our context
    """
    return dict(STRIPE_PUBLIC_KEY=get_stripe_credentials()[0])
