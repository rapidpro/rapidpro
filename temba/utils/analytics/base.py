import abc
import logging

from django.conf import settings
from django.template import Context, Engine
from django.utils.safestring import mark_safe

logger = logging.getLogger(__name__)


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


class ConsoleBackend(AnalyticsBackend):
    """
    An example analytics backend which just prints to the console
    """

    slug = "console"

    def gauge(self, event: str, value):
        if not settings.TESTING:  # pragma: no cover
            print(f"[analytics] gauge={event} value={value}")

    def track(self, user, event: str, properties: dict):
        if not settings.TESTING:  # pragma: no cover
            print(f"[analytics] event={event} user={user.email}")


def get_backends() -> list:
    from . import backends

    return list(backends.values())


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
