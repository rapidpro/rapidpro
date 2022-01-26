from django.conf.urls import include
from django.urls import re_path

from .models import IntegrationType
from .views import (
    ConfirmAccessView,
    LoginView,
    OrgCRUDL,
    SpaView,
    StripeHandler,
    TopUpCRUDL,
    TwoFactorBackupView,
    TwoFactorVerifyView,
    UserCRUDL,
    check_login,
)

urlpatterns = OrgCRUDL().as_urlpatterns()
urlpatterns += TopUpCRUDL().as_urlpatterns()
urlpatterns += UserCRUDL().as_urlpatterns()

# we iterate all our integration types, finding all the URLs they want to wire in
integration_type_urls = []

for integration in IntegrationType.get_all():
    integration_urls = integration.get_urls()
    for u in integration_urls:
        u.name = f"integrations.{integration.slug}.{u.name}"

    if integration_urls:
        integration_type_urls.append(re_path("^%s/" % integration.slug, include(integration_urls)))


spa = SpaView.as_view()

urlpatterns += [
    re_path(r"^login/$", check_login, name="users.user_check_login"),
    re_path(r"^users/login/$", LoginView.as_view(), name="users.login"),
    re_path(r"^users/two-factor/verify/$", TwoFactorVerifyView.as_view(), name="users.two_factor_verify"),
    re_path(r"^users/two-factor/backup/$", TwoFactorBackupView.as_view(), name="users.two_factor_backup"),
    re_path(r"^users/confirm-access/$", ConfirmAccessView.as_view(), name="users.confirm_access"),
    re_path(r"^handlers/stripe/$", StripeHandler.as_view(), name="handlers.stripe_handler"),
    re_path(r"^integrations/", include(integration_type_urls)),
    # for backwards compatibility
    re_path(r"^api/v1/stripe/$", StripeHandler.as_view()),
    # for spa
    re_path(r"^(?P<level_0>contacts|tickets|messages|channels|flows|plugins|settings)/$", spa, name="spa"),
    re_path(
        r"^(?P<level_0>contacts|tickets|messages|channels|flows|plugins|settings)/(?P<level_1>.+)/$",
        spa,
        name="spa.level_1",
    ),
    re_path(
        r"^(?P<level_0>contacts|tickets|messages|channels|flows|plugins|settings)/(?P<level_1>\w+)/(?P<level_2>.+)/$",
        spa,
        name="spa.level_2",
    ),
    re_path(
        r"^(?P<level_0>contacts|tickets|messages|channels|flows|plugins|settings)/(?P<level_1>\w+)/(?P<level_2>.+)/.*$",
        spa,
        name="spa.level_max",
    ),
]
