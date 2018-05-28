
import logging
import time

import analytics as segment_analytics
from intercom.client import Client as IntercomClient
from librato_bg import Client as LibratoClient

from django.conf import settings

logger = logging.getLogger(__name__)

# our librato_bg client
_librato = None

# our intercom client
_intercom = None

# whether segment is active
_segment = False


def init_analytics():  # pragma: no cover
    """
    Initializes our analytics libraries based on our settings
    """
    # configure Segment if configured
    segment_key = getattr(settings, "SEGMENT_IO_KEY", "")
    if segment_key:
        global _segment
        segment_analytics.send = settings.IS_PROD
        segment_analytics.debug = not settings.IS_PROD
        segment_analytics.write_key = segment_key
        _segment = True

    # configure Intercom if configured
    intercom_key = getattr(settings, "INTERCOM_TOKEN", "")
    if intercom_key:
        global _intercom
        _intercom = IntercomClient(personal_access_token=intercom_key)

    # configure Librato if configured
    librato_user = getattr(settings, "LIBRATO_USER", None)
    librato_token = getattr(settings, "LIBRATO_TOKEN", None)
    if librato_user and librato_token:
        global _librato
        _librato = LibratoClient(librato_user, librato_token)


def gauge(event, value=None):  # pragma: no cover
    """
    Triggers a gauge event in Librato
    """
    if value is None:
        value = 1

    # settings.HOSTNAME is actually service name (like textit.in), and settings.MACHINE_NAME is the name of the machine
    # (virtual/physical) that is part of the service
    reporting_hostname = "%s.%s" % (settings.MACHINE_HOSTNAME, settings.HOSTNAME)

    if _librato:
        _librato.gauge(event, value, reporting_hostname)


def identify(email, name, attributes):  # pragma: no cover
    """
    Creates and identifies a new user to our analytics backends. It is ok to call this with an
    existing user, their name and attributes will just be updated.
    """
    # no op if we aren't prod
    if not settings.IS_PROD:
        return

    # post to segment if configured
    if _segment:
        segment_analytics.identify(email, attributes)

    # post to intercom if configured
    if _intercom:
        try:
            # rip out duplicate fields for intercom
            for key in ("first_name", "last_name", "email"):
                attributes.pop(key, None)

            _intercom.users.create(email=email, name=name, custom_attributes=attributes)
        except:
            logger.error("error posting to intercom", exc_info=True)


def track(email, event, properties=None, context=None):  # pragma: no cover
    """
    Tracks the passed in event for the passed in user in all configured analytics backends.
    """
    # no op if we aren't prod
    if not settings.IS_PROD:
        return

    # post to segment if configured
    if _segment:
        # create a context if none was passed in
        if context is None:
            context = dict()

        # set our source according to our hostname (name of the platform instance, and not machine hostname)
        context["source"] = settings.HOSTNAME

        # create properties if none were passed in
        if properties is None:
            properties = dict()

        # populate value=1 in our properties if it isn't present
        if "value" not in properties:
            properties["value"] = 1

        # call through to the real segment.io analytics
        segment_analytics.track(email, event, properties, context)

    # post to intercom if configured
    if _intercom:
        try:
            _intercom.events.create(
                event_name=event,
                created_at=int(time.mktime(time.localtime())),
                email=email,
                metadata=properties if properties else {},
            )
        except:
            logger.error("error posting to intercom", exc_info=True)
