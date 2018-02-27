# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time

from django.conf import settings
from django.core.urlresolvers import resolve
from django.http import HttpResponseRedirect
from temba.orgs.context_processors import user_group_perms_processor


class FormaxMixin(object):

    def derive_formax_sections(self, formax, context):  # pragma: needs cover
        return None

    def get_context_data(self, *args, **kwargs):
        context = super(FormaxMixin, self).get_context_data(*args, **kwargs)
        formax = Formax(self.request)
        self.derive_formax_sections(formax, context)

        if len(formax.sections) > 0:
            context['formax'] = formax
        return context


class Formax(object):

    def __init__(self, request):
        self.sections = []
        self.request = request
        context = user_group_perms_processor(self.request)
        self.org = context['user_org']

    def add_section(self, name, url, icon, action='formax', button='Save', nobutton=False, dependents=None):
        resolver = resolve(url)
        self.request.META['HTTP_X_FORMAX'] = 1
        self.request.META['HTTP_X_PJAX'] = 1

        start = time.time()

        open = self.request.GET.get('open', None)
        if open == name:  # pragma: needs cover
            action = 'open'

        response = resolver.func(self.request, *resolver.args, **resolver.kwargs)

        # redirects don't do us any good
        if not isinstance(response, HttpResponseRedirect):
            response.render()
            self.sections.append(dict(name=name, url=url, response=response.content,
                                      icon=icon, action=action, button=button, nobutton=nobutton, dependents=dependents))

        if settings.DEBUG:
            print("%s took: %f" % (url, time.time() - start))
