from django.conf.urls import include, url

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
        integration_type_urls.append(url("^%s/" % integration.slug, include(integration_urls)))


spa = SpaView.as_view()

urlpatterns += [
    url(r"^login/$", check_login, name="users.user_check_login"),
    url(r"^users/login/$", LoginView.as_view(), name="users.login"),
    url(r"^users/two-factor/verify/$", TwoFactorVerifyView.as_view(), name="users.two_factor_verify"),
    url(r"^users/two-factor/backup/$", TwoFactorBackupView.as_view(), name="users.two_factor_backup"),
    url(r"^users/confirm-access/$", ConfirmAccessView.as_view(), name="users.confirm_access"),
    url(r"^handlers/stripe/$", StripeHandler.as_view(), name="handlers.stripe_handler"),
    url(r"^integrations/", include(integration_type_urls)),
    # for backwards compatibility
    url(r"^api/v1/stripe/$", StripeHandler.as_view()),
    # for spa
    url(r"^(?P<level_0>contacts|tickets|messages|channels)/$", spa, name="spa"),
    url(r"^(?P<level_0>contacts|tickets|messages|channels)/(?P<level_1>\w+)/$", spa, name="spa.level_1"),
    url(
        r"^(?P<level_0>contacts|tickets|messages|channels)/(?P<level_1>\w+)/(?P<level_2>.+)/$", spa, name="spa.level_2"
    ),
]
