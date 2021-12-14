import hashlib
import hmac
import logging
import time
from random import randint

import analytics as segment_analytics
from crisp_api import Crisp
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

# our crisp client if configured
_crisp = None

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
        segment_analytics.send = True
        segment_analytics.debug = False
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

    crisp_identifier = getattr(settings, "CRISP_IDENTIFIER", None)
    crisp_key = getattr(settings, "CRISP_KEY", None)
    crisp_website_id = getattr(settings, "CRISP_WEBSITE_ID", None)
    if crisp_identifier and crisp_key and crisp_website_id:
        global _crisp
        _crisp = Crisp()
        _crisp.website_id = crisp_website_id
        _crisp.set_tier("plugin")
        _crisp.authenticate(crisp_identifier, crisp_key)


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

    attributes = dict(
        email=user.username,
        first_name=user.first_name,
        segment=randint(1, 10),
        last_name=user.last_name,
        brand=brand["slug"] if brand else None,
    )
    user_name = f"{user.first_name} {user.last_name}"
    email = user.email if user.email else user.username

    if org:
        attributes["org"] = org.name
        attributes["paid"] = org.account_value()

    # post to segment if configured
    if _segment:  # pragma: no cover
        segment_analytics.identify(email, attributes)

    # post to intercom if configured
    if _intercom:
        try:
            # rip out duplicate fields for intercom
            for key in ("first_name", "last_name", "email"):
                attributes.pop(key, None)

            intercom_user = _intercom.users.create(email=email, name=user_name, custom_attributes=attributes)
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

    if _crisp:

        user_settings = user.get_settings()
        existing_profile = None
        external_id = user_settings.external_id
        segments = [attributes["brand"], f"random-{attributes['segment']}"]

        try:
            existing_profile = _crisp.website.get_people_profile(_crisp.website_id, email)
            segments = existing_profile["segments"]
            external_id = existing_profile["people_id"]

            segments.push(attributes["brand"])
            randoms = [seg for seg in segments if seg.startswith("random-")]
            if not randoms:
                segments.append(f"random-{attributes['segment']}")

        except Exception:
            pass

        data = {"person": {"nickname": user_name}, "segments": segments}

        if org and brand:
            data["company"] = {
                "name": org.name,
                "url": f"https://{brand['host']}/org/update/{org.id}/",
                "domain": f"{brand['host']}/org/update/{org.id}",
            }

        try:
            if existing_profile:
                _crisp.website.update_people_profile(_crisp.website_id, email, data)
            else:
                data["email"] = email
                response = _crisp.website.add_new_people_profile(_crisp.website_id, data)
                external_id = response["people_id"]

            support_secret = getattr(settings, "SUPPORT_SECRET", "")
            signature = hmac.new(
                bytes(support_secret, "latin-1"),
                msg=bytes(email, "latin-1"),
                digestmod=hashlib.sha256,
            ).hexdigest()

            user_settings = user.get_settings()
            user_settings.verification_token = signature
            if external_id:
                user_settings.external_id = external_id
            user_settings.save()

        except Exception:  # pragma: no cover
            logger.error("error posting to crisp", exc_info=True)


def set_orgs(email, all_orgs):
    """
    Sets a user's orgs to canonical set of orgs it they aren't archived
    """

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
    change_date = json.encode_datetime(timezone.now())

    if _intercom:
        try:

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

    if _crisp:

        consented_segment = "consented"

        try:
            profile = _crisp.website.get_people_profile(_crisp.website_id, email)
            segments = profile["segments"]

            previous_segment_count = len(segments)
            previously_consented = "consented" in segments

            # we need to remove an existing consent
            if not consent and previously_consented:
                segments = [seg for seg in segments if seg != consented_segment]

            # we need to add a new consent
            if consent and not previously_consented:
                segments.append(consented_segment)

            # update our segment data if necessary
            if len(segments) != previous_segment_count:
                _crisp.website.update_people_profile(_crisp.website_id, email, {"segments": segments})

            # this would be better as an update which merges, but v1.10 of the python client doesn't support that yet
            data = _crisp.website.get_people_data(_crisp.website_id, email)["data"]
            data[f"consent_changed"] = change_date
            _crisp.website.save_people_data(_crisp.website_id, email, {"data": data})

            # add an event for acting on this, not that events are ephemeral
            if consent:
                _crisp.website.add_people_event(
                    _crisp.website_id, email, {"color": "green", "text": f"Consent granted"}
                )
            else:
                _crisp.website.add_people_event(_crisp.website_id, email, {"color": "red", "text": f"Consent revoked"})

        except Exception:  # pragma: no cover
            logger.error("error accessing crisp", exc_info=True)


def track(user, event_name, properties=None, context=None):
    """
    Tracks the passed in event for the passed in user in all configured analytics backends.
    """

    # no op for anon user
    if not user.is_authenticated:
        return

    email = user.email

    if properties is None:
        properties = {}
    properties = {k: v for k, v in properties.items() if v is not None}

    # post to segment if configured
    if _segment:  # pragma: no cover
        # create a context if none was passed in
        if context is None:
            context = dict()

        # set our source according to our hostname (name of the platform instance, and not machine hostname)
        context["source"] = settings.HOSTNAME

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
                metadata=properties,
            )
        except Exception:
            logger.error("error posting to intercom", exc_info=True)

    if _crisp:

        color = "grey"

        if "signup" in event_name:
            color = "green"

        if "created" in event_name:
            color = "blue"

        if "export" in event_name or "import" in event_name:
            color = "purple"

        try:
            _crisp.website.add_people_event(
                _crisp.website_id,
                email,
                {"color": color, "text": event_name, "data": properties},
            )
        except Exception:  # pragma: no cover
            logger.error("error posting to crisp", exc_info=True)
