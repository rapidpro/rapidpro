import logging
import time
from random import randint

import analytics as segment_analytics
from intercom.client import Client as IntercomClient
from intercom.errors import ResourceNotFound
from librato_bg import Client as LibratoClient

from django.conf import settings
from django.utils import timezone

from temba.utils import json

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


def get_intercom_user(email):
    try:
        return _intercom.users.find(email=email)
    except ResourceNotFound:
        pass


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


def identify_org(org, attributes=None):
    """
    Creates and identifies an org on our analytics backends where appropriate
    """
    if not settings.IS_PROD:
        return

    if not attributes:
        attributes = {}

    if _intercom:

        intercom_attributes = {}
        for key in ("monthly_spend", "industry", "website"):
            value = attributes.pop(key, None)
            if value:
                intercom_attributes[key] = value

        attributes["brand"] = org.brand
        attributes["org_id"] = org.id

        _intercom.companies.create(
            company_id=org.id,
            name=org.name,
            created_at=json.encode_datetime(org.created_on),
            custom_attributes=attributes,
            **intercom_attributes,
        )


def identify(user, brand, org):
    """
    Creates and identifies a new user to our analytics backends. It is ok to call this with an
    existing user, their name and attributes will just be updated.
    """
    # no op if we aren't prod
    if not settings.IS_PROD:
        return

    attributes = dict(
        email=user.username, first_name=user.first_name, segment=randint(1, 10), last_name=user.last_name, brand=brand
    )
    user_name = f"{user.first_name} {user.last_name}"
    if org:
        attributes["org"] = org.name
        attributes["paid"] = org.account_value()

    # post to segment if configured
    if _segment:  # pragma: no cover
        segment_analytics.identify(user.username, attributes)

    # post to intercom if configured
    if _intercom:
        try:
            # rip out duplicate fields for intercom
            for key in ("first_name", "last_name", "email"):
                attributes.pop(key, None)

            intercom_user = _intercom.users.create(email=user.username, name=user_name, custom_attributes=attributes)

            intercom_user.companies = [
                dict(
                    company_id=org.id,
                    name=org.name,
                    created_at=json.encode_datetime(org.created_on),
                    custom_attributes=dict(brand=org.brand, org_id=org.id),
                )
            ]

            _intercom.users.save(intercom_user)
        except Exception:
            logger.error("error posting to intercom", exc_info=True)


def set_orgs(email, all_orgs):
    """
    Sets a user's orgs to canonical set of orgs it they aren't archived
    """

    # no op if we aren't prod
    if not settings.IS_PROD:
        return

    if _intercom:
        intercom_user = get_intercom_user(email)

        # if the user is archived this is a noop
        if intercom_user:
            companies = [dict(company_id=org.id, name=org.name) for org in all_orgs]

            for company in intercom_user.companies:
                if not any(int(company.company_id) == c.get("company_id") for c in companies):
                    companies.append(dict(company_id=company.company_id, remove=True))

            intercom_user.companies = companies
            _intercom.users.save(intercom_user)


def change_consent(email, consent):
    """
    Notifies analytics backends of a user's consent status.
    """
    # no op if we aren't prod
    if not settings.IS_PROD:
        return

    if _intercom:
        try:
            change_date = json.encode_datetime(timezone.now())

            user = get_intercom_user(email)

            if consent:
                if not user or not user.custom_attributes.get("consent", False):
                    _intercom.users.create(
                        email=email, custom_attributes=dict(consent=consent, consent_changed=change_date)
                    )
            else:
                if user:
                    _intercom.users.create(
                        email=email, custom_attributes=dict(consent=consent, consent_changed=change_date)
                    )

                    # this archives a user on intercom so they are no longer processed
                    _intercom.users.delete(user)

        except Exception:
            logger.error("error posting to intercom", exc_info=True)


def track(email, event_name, properties=None, context=None):
    """
    Tracks the passed in event for the passed in user in all configured analytics backends.
    """
    # no op if we aren't prod
    if not settings.IS_PROD:
        return

    # post to segment if configured
    if _segment:  # pragma: no cover
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
        segment_analytics.track(email, event_name, properties, context)

    # post to intercom if configured
    if _intercom:
        try:
            _intercom.events.create(
                event_name=event_name,
                created_at=int(time.mktime(time.localtime())),
                email=email,
                metadata=properties if properties else {},
            )
        except Exception:
            logger.error("error posting to intercom", exc_info=True)
