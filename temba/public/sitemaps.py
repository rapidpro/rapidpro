# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.contrib.sitemaps import Sitemap
from django.core.urlresolvers import reverse
from temba.settings import SITEMAP
from .models import Video


class PublicViewSitemap(Sitemap):
    priority = 0.5
    changefreq = 'daily'

    def items(self):
        return SITEMAP

    def location(self, item):
        return reverse(item)


class VideoSitemap(Sitemap):
    priority = 0.5
    changefreq = 'daily'

    def items(self):
        return Video.objects.filter(is_active=True)

    def location(self, item):
        return reverse('public.video_read', args=[item.pk])
