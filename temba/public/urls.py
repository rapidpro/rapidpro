from django.conf.urls import url
from django.contrib.sitemaps.views import sitemap
from django.views.decorators.csrf import csrf_exempt

from temba.settings import DEBUG

from .sitemaps import PublicViewSitemap, VideoSitemap
from .views import (
    Android,
    Blog,
    GenerateCoupon,
    IndexView,
    LeadCRUDL,
    LeadViewer,
    OrderStatus,
    Style,
    VideoCRUDL,
    Welcome,
    WelcomeRedirect,
)

sitemaps = {"public": PublicViewSitemap, "video": VideoSitemap}

urlpatterns = [
    url(r"^$", IndexView.as_view(), {}, "public.public_index"),
    url(r"^sitemap\.xml$", sitemap, {"sitemaps": sitemaps}, name="public.sitemaps"),
    url(r"^blog/$", Blog.as_view(), {}, "public.public_blog"),
    url(r"^welcome/$", Welcome.as_view(), {}, "public.public_welcome"),
    url(r"^android/$", Android.as_view(), {}, "public.public_android"),
    url(r"^public/welcome/$", WelcomeRedirect.as_view(), {}, "public.public_welcome_redirect"),
    url(r"^demo/status/$", csrf_exempt(OrderStatus.as_view()), {}, "demo.order_status"),
    url(r"^demo/coupon/$", csrf_exempt(GenerateCoupon.as_view()), {}, "demo.generate_coupon"),
]

if DEBUG:  # pragma: needs cover
    urlpatterns.append(url(r"^style/$", Style.as_view(), {}, "public.public_style")),


urlpatterns += LeadCRUDL().as_urlpatterns()
urlpatterns += LeadViewer().as_urlpatterns()
urlpatterns += VideoCRUDL().as_urlpatterns()
