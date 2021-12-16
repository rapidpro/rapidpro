import abc
import logging

from django.conf import settings

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


def init_analytics():
    """
    Initializes our analytics libraries based on our settings
    """

    from .crisp import CrispBackend
    from .librato import LibratoBackend

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
