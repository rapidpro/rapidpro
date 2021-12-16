import abc
import hashlib
import hmac
import logging
from random import randint

from crisp_api import Crisp
from librato_bg import Client as LibratoClient

from django.conf import settings
from django.utils import timezone

from temba.utils import json

logger = logging.getLogger(__name__)

REGISTERED_BACKENDS = []


class AnalyticsBackend(metaclass=abc.ABCMeta):
    def gauge(self, event: str, value):
        """
        Records a gauge value
        """

    def track(self, user, event: str, properties: dict):
        """
        Tracks a user event
        """

    def identify(self, user, brand, org):
        """
        Creates and identifies a new user
        """

    def change_consent(self, user, consent: bool):
        """
        Notifies of a user's consent status.
        """

    def get_template_context(self) -> dict:
        """
        Gets any template context
        """


class LibratoBackend(AnalyticsBackend):
    def __init__(self, user, token):
        self.client = LibratoClient(user, token)

    def gauge(self, event: str, value):
        source = f"{settings.MACHINE_HOSTNAME}.{settings.HOSTNAME}"  # e.g. rapid1.rapidpro.io
        self.client.gauge(event, value, source)

    def track(self, user, event: str, properties: dict):
        pass

    def identify(self, user, brand, org):
        pass

    def change_consent(self, user, consent: bool):
        pass

    def get_template_context(self) -> dict:
        return {}


class CrispBackend(AnalyticsBackend):
    def __init__(self, identifier: str, key: str, website_id: str):
        self.client = Crisp()
        self.client.set_tier("plugin")
        self.client.authenticate(identifier, key)
        self.website_id = website_id

    def gauge(self, event: str, value):
        pass

    def track(self, user, event: str, properties: dict):
        email = user.email
        properties = {k: v for k, v in properties.items() if v is not None}

        color = "grey"
        if "signup" in event:
            color = "green"
        if "created" in event:
            color = "blue"
        if "export" in event or "import" in event:
            color = "purple"

        try:
            self.client.website.add_people_event(
                self.website_id, email, {"color": color, "text": event, "data": properties}
            )
        except Exception:  # pragma: no cover
            logger.error("error posting to crisp", exc_info=True)

    def identify(self, user, brand, org):
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

        user_settings = user.get_settings()
        existing_profile = None
        external_id = user_settings.external_id
        segments = [attributes["brand"], f"random-{attributes['segment']}"]

        try:
            existing_profile = self.client.website.get_people_profile(self.website_id, email)
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
                self.client.website.update_people_profile(self.website_id, email, data)
            else:
                data["email"] = email
                response = self.client.website.add_new_people_profile(self.website_id, data)
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

    def change_consent(self, user, consent: bool):
        email = user.email
        change_date = json.encode_datetime(timezone.now())
        consented_segment = "consented"

        try:
            profile = self.client.website.get_people_profile(self.website_id, email)
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
                self.client.website.update_people_profile(self.website_id, email, {"segments": segments})

            # this would be better as an update which merges, but v1.10 of the python client doesn't support that yet
            data = self.client.website.get_people_data(self.website_id, email)["data"]
            data[f"consent_changed"] = change_date
            self.client.website.save_people_data(self.website_id, email, {"data": data})

            # add an event for acting on this, not that events are ephemeral
            if consent:
                self.client.website.add_people_event(
                    self.website_id, email, {"color": "green", "text": f"Consent granted"}
                )
            else:
                self.client.website.add_people_event(
                    self.website_id, email, {"color": "red", "text": f"Consent revoked"}
                )

        except Exception:  # pragma: no cover
            logger.error("error accessing crisp", exc_info=True)

    def get_template_context(self) -> dict:
        return {"crisp_website_id": settings.CRISP_WEBSITE_ID}


def init_analytics():
    """
    Initializes our analytics libraries based on our settings
    """

    # configure Librato if configured
    librato_user = getattr(settings, "LIBRATO_USER", None)
    librato_token = getattr(settings, "LIBRATO_TOKEN", None)
    if librato_user and librato_token:
        REGISTERED_BACKENDS.append(LibratoBackend(librato_user, librato_token))

    crisp_identifier = getattr(settings, "CRISP_IDENTIFIER", None)
    crisp_key = getattr(settings, "CRISP_KEY", None)
    crisp_website_id = getattr(settings, "CRISP_WEBSITE_ID", None)
    if crisp_identifier and crisp_key and crisp_website_id:
        REGISTERED_BACKENDS.append(CrispBackend(crisp_identifier, crisp_key, crisp_website_id))


def gauge(event: str, value):
    """
    Reports a gauge value
    """
    for backend in REGISTERED_BACKENDS:
        backend.gauge(event, value)


def identify(user, brand, org):
    """
    Creates and identifies a new user to our analytics backends
    """
    for backend in REGISTERED_BACKENDS:
        backend.identify(user, brand, org)


def change_consent(user, consent: bool):
    """
    Notifies analytics backends of a user's consent status.
    """
    for backend in REGISTERED_BACKENDS:
        backend.change_consent(user, consent)


def track(user, event_name: str, properties: dict = None):
    """
    Tracks the passed in event for the passed in user in all configured analytics backends.
    """

    if not user.is_authenticated:  # no op for anon user
        return

    for backend in REGISTERED_BACKENDS:
        backend.track(user, event_name, properties or {})


def get_template_context() -> dict:
    context = {}
    for backend in REGISTERED_BACKENDS:
        context.update(**backend.get_template_context())
    return context
