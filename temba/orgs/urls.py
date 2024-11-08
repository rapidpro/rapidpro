from django.conf import settings
from django.conf.urls import include
from django.contrib.auth.views import LogoutView
from django.urls import re_path
from django.views.generic import RedirectView

from .models import IntegrationType
from .views import (
    ConfirmAccessView,
    ExportCRUDL,
    InvitationCRUDL,
    LoginView,
    OrgCRUDL,
    OrgImportCRUDL,
    TwoFactorBackupView,
    TwoFactorVerifyView,
    UserCRUDL,
    check_login,
)

logout_url = getattr(settings, "LOGOUT_REDIRECT_URL", None)

urlpatterns = OrgCRUDL().as_urlpatterns()
urlpatterns += OrgImportCRUDL().as_urlpatterns()
urlpatterns += UserCRUDL().as_urlpatterns()
urlpatterns += InvitationCRUDL().as_urlpatterns()
urlpatterns += ExportCRUDL().as_urlpatterns()

# we iterate all our integration types, finding all the URLs they want to wire in
integration_type_urls = []

for integration in IntegrationType.get_all():
    integration_urls = integration.get_urls()
    for u in integration_urls:
        u.name = f"integrations.{integration.slug}.{u.name}"

    if integration_urls:
        integration_type_urls.append(re_path("^%s/" % integration.slug, include(integration_urls)))

urlpatterns += [
    re_path(r"^login/$", check_login, name="orgs.check_login"),
    re_path(r"^users/login/$", LoginView.as_view(), name="orgs.user_login"),
    re_path(
        r"^users/logout/$",
        LogoutView.as_view(),
        dict(redirect_field_name="go", next_page=logout_url),
        name="orgs.user_logout",
    ),
    re_path(r"^users/two-factor/verify/$", TwoFactorVerifyView.as_view(), name="orgs.two_factor_verify"),
    re_path(r"^users/two-factor/backup/$", TwoFactorBackupView.as_view(), name="orgs.two_factor_backup"),
    re_path(r"^users/confirm-access/$", ConfirmAccessView.as_view(), name="orgs.confirm_access"),
    re_path(r"^integrations/", include(integration_type_urls)),
    # TODO rework login/logout to not need these redirects
    re_path(
        r"^users/user/forget/$",
        RedirectView.as_view(pattern_name="orgs.user_forget", permanent=True),
        name="users.user_forget",
    ),
    re_path(
        r"^users/user/recover/(?P<token>\w+)/$",
        RedirectView.as_view(pattern_name="orgs.user_recover", permanent=True),
        name="users.user_recover",
    ),
    re_path(
        r"^users/user/failed/$",
        RedirectView.as_view(pattern_name="orgs.user_failed", permanent=True),
        name="users.user_failed",
    ),
]
