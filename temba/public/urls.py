from __future__ import unicode_literals

from django.conf.urls import patterns
from django.views.decorators.csrf import csrf_exempt
from .sitemaps import PublicViewSitemap, VideoSitemap
from .views import LeadCRUDL, LeadViewer, VideoCRUDL
from .views import IndexView, Blog, Welcome, Deploy, Privacy, WelcomeRedirect, OrderStatus, GenerateCoupon


sitemaps = {
    'public': PublicViewSitemap,
    'video': VideoSitemap
}

urlpatterns = patterns('',
                       (r'^$', IndexView.as_view(), {}, 'public.public_index'),
                       (r'^sitemap\.xml$', 'django.contrib.sitemaps.views.sitemap',
                        {'sitemaps': sitemaps}, 'public.sitemaps'),
                       (r'^blog/$', Blog.as_view(), {}, 'public.public_blog'),

                       (r'^welcome/$', Welcome.as_view(), {}, 'public.public_welcome'),
                       (r'^deploy/$', Deploy.as_view(), {}, 'public.public_deploy'),
                       (r'^privacy/$', Privacy.as_view(), {}, 'public.public_privacy'),

                       (r'^public/welcome/$', WelcomeRedirect.as_view(), {}, 'public.public_welcome_redirect'),
                       (r'^demo/status/$', csrf_exempt(OrderStatus.as_view()), {}, 'demo.order_status'),
                       (r'^demo/coupon/$', csrf_exempt(GenerateCoupon.as_view()), {}, 'demo.generate_coupon'))

urlpatterns += LeadCRUDL().as_urlpatterns()
urlpatterns += LeadViewer().as_urlpatterns()
urlpatterns += VideoCRUDL().as_urlpatterns()
