# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import analytics as segment_analytics

from django.conf import settings
from librato_bg import Client

# our librato_bg client
_librato = None


def init_librato(user, token):
    global _librato
    _librato = Client(user, token)  # pragma: needs cover


def gauge(event, value=None):
    """
    Triggers a gauge event in Librato
    """
    if value is None:
        value = 1

    # settings.HOSTNAME is actually service name (like textit.in), and settings.MACHINE_NAME is the name of the machine
    # (virtual/physical) that is part of the service
    reporting_hostname = '%s.%s' % (settings.MACHINE_HOSTNAME, settings.HOSTNAME)

    if _librato:
        _librato.gauge(event, value, reporting_hostname)  # pragma: needs cover


def identify(username, attributes):
    """
    Pass through to segment.io analytics.
    """
    segment_analytics.identify(username, attributes)


def track(user, event, properties=None, context=None):  # pragma: needs cover
    """
    Helper function that wraps the segment.io track and adds in the source
    for the event as our current hostname.
    """
    # no op if we aren't prod
    if not settings.IS_PROD:
        return

    # create a context if none was passed in
    if context is None:
        context = dict()

    # set our source according to our hostname (name of the platform instance, and not machine hostname)
    context['source'] = settings.HOSTNAME

    # create properties if none were passed in
    if properties is None:
        properties = dict()

    # populate value=1 in our properties if it isn't present
    if 'value' not in properties:
        properties['value'] = 1

    # call through to the real segment.io analytics
    segment_analytics.track(user, event, properties, context)
