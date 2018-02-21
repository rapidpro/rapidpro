# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.conf.urls import url
from django.views.decorators.csrf import csrf_exempt

from django.contrib.sitemaps.views import sitemap
from .sitemaps import PublicViewSitemap, VideoSitemap
from .views import LeadCRUDL, LeadViewer, VideoCRUDL
from .views import IndexView, Blog, Welcome, Deploy, Privacy, WelcomeRedirect, OrderStatus, GenerateCoupon


sitemaps = {
    'public': PublicViewSitemap,
    'video': VideoSitemap
}

urlpatterns = [
    url(r'^$', IndexView.as_view(), {}, 'public.public_index'),
    url(r'^sitemap\.xml$', sitemap, {'sitemaps': sitemaps}, name='public.sitemaps'),
    url(r'^blog/$', Blog.as_view(), {}, 'public.public_blog'),

    url(r'^welcome/$', Welcome.as_view(), {}, 'public.public_welcome'),
    url(r'^deploy/$', Deploy.as_view(), {}, 'public.public_deploy'),
    url(r'^privacy/$', Privacy.as_view(), {}, 'public.public_privacy'),

    url(r'^public/welcome/$', WelcomeRedirect.as_view(), {}, 'public.public_welcome_redirect'),
    url(r'^demo/status/$', csrf_exempt(OrderStatus.as_view()), {}, 'demo.order_status'),
    url(r'^demo/coupon/$', csrf_exempt(GenerateCoupon.as_view()), {}, 'demo.generate_coupon'),
]

urlpatterns += LeadCRUDL().as_urlpatterns()
urlpatterns += LeadViewer().as_urlpatterns()
urlpatterns += VideoCRUDL().as_urlpatterns()
