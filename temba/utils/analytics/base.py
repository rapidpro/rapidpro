import abc
import logging

from django.conf import settings
from django.template import Context, Engine
from django.utils.safestring import mark_safe

logger = logging.getLogger(__name__)

registered_backends = []


class AnalyticsBackend(metaclass=abc.ABCMeta):
    slug: str = None
    hook_templates = {}

    def gauge(self, event: str, value):
        """
        Records a gauge value
        """

    def track(self, user, event: str, properties: dict):
        """
        Tracks a user event
        """

    def identify(self, user, brand: dict, org):
        """
        Creates and identifies a new user
        """

    def change_consent(self, user, consent: bool):
        """
        Notifies of a user's consent status.
        """

    def get_hook_template(self, name: str) -> str:
        """
        Gets template name for named hook
        """
        return self.hook_templates.get(name)

    def get_hook_context(self, request) -> dict:
        """
        Gets context to be included in hook templates
        """
        return {}


def init():  # pragma: no cover
    """
    Initializes our analytics backends based on our settings.
    TODO this should all be dynamic based on a setting which is a list of backend class names
    """

    from .crisp import CrispBackend
    from .librato import LibratoBackend

    registered_backends.clear()

    # configure Librato if configured
    librato_user = getattr(settings, "LIBRATO_USER", None)
    librato_token = getattr(settings, "LIBRATO_TOKEN", None)
    if librato_user and librato_token:
        registered_backends.append(LibratoBackend(librato_user, librato_token))

    crisp_identifier = getattr(settings, "CRISP_IDENTIFIER", None)
    crisp_key = getattr(settings, "CRISP_KEY", None)
    crisp_website_id = getattr(settings, "CRISP_WEBSITE_ID", None)
    if crisp_identifier and crisp_key and crisp_website_id:
        registered_backends.append(CrispBackend(crisp_identifier, crisp_key, crisp_website_id))


def get_backends() -> list:
    return registered_backends


def gauge(event: str, value):
    """
    Reports a gauge value
    """
    for backend in get_backends():
        try:
            backend.gauge(event, value)
        except Exception:
            logger.exception(f"error updating gauge on {backend.slug}")


def identify(user, brand, org):
    """
    Creates and identifies a new user to our analytics backends
    """
    for backend in get_backends():
        try:
            backend.identify(user, brand, org)
        except Exception:
            logger.exception(f"error identifying user on {backend.slug}")


def change_consent(user, consent: bool):
    """
    Notifies analytics backends of a user's consent status.
    """
    for backend in get_backends():
        try:
            backend.change_consent(user, consent)
        except Exception:
            logger.exception(f"error changing consent on {backend.slug}")


def track(user, event: str, properties: dict = None):
    """
    Tracks the passed in event for the passed in user in all configured analytics backends.
    """

    if not user.is_authenticated:  # no op for anon user
        return

    for backend in get_backends():
        try:
            backend.track(user, event, properties or {})
        except Exception:
            logger.exception(f"error tracking event on {backend.slug}")


def get_hook_html(name: str, context) -> str:
    """
    Gets HTML to be inserted at the named template hook
    """
    engine = Engine.get_default()
    html = ""
    for backend in get_backends():
        template_name = backend.get_hook_template(name)
        if template_name:
            with context.update(backend.get_hook_context(context.request)):
                html += f"<!-- begin hook for {backend.slug} -->\n"
                html += engine.get_template(template_name).render(Context(context))
                html += f"<!-- end hook for {backend.slug} -->\n"

    return mark_safe(html)
