import logging
from urllib.parse import quote, urlencode

from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


class NoNavMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["base_template"] = "no_nav.html"
        return context


class NonAtomicMixin:
    """
    Utility mixin to disable automatic transaction wrapping of a class based view
    """

    @transaction.non_atomic_requests
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class PostOnlyMixin:
    """
    Utility mixin to make a class based view be POST only
    """

    def get(self, *args, **kwargs):
        return HttpResponse("Method Not Allowed", status=405)


class RequireRecentAuthMixin:
    """
    Mixin that redirects the user to a authentication page if they haven't authenticated recently.
    """

    recent_auth_seconds = 10 * 60
    recent_auth_includes_formax = False

    def pre_process(self, request, *args, **kwargs):
        is_formax = "HTTP_X_FORMAX" in request.META
        if not is_formax or self.recent_auth_includes_formax:
            last_auth_on = request.user.settings.last_auth_on
            if not last_auth_on or (timezone.now() - last_auth_on).total_seconds() > self.recent_auth_seconds:
                return HttpResponseRedirect(reverse("users.confirm_access") + f"?next={quote(request.path)}")

        return super().pre_process(request, *args, **kwargs)


class StaffOnlyMixin:
    """
    Views that only staff should be able to access
    """

    def has_permission(self, request, *args, **kwargs):
        return self.request.user.is_staff


class ContentMenuMixin:
    """
    Mixin for views that have a content menu (hamburger icon with dropdown items)
    """

    class Menu:
        """
        Utility for building content menus
        """

        def __init__(self):
            self.groups = [[]]

        def new_group(self):
            self.groups.append([])

        def add_link(self, label: str, url: str, as_button: bool = False):
            self.groups[-1].append({"type": "link", "label": label, "url": url, "as_button": as_button})

        def add_js(self, id: str, label: str, as_button: bool = False):
            self.groups[-1].append(
                {
                    "id": id,
                    "type": "js",
                    "label": label,
                    "as_button": as_button,
                }
            )

        def add_url_post(self, label: str, url: str, as_button: bool = False):
            self.groups[-1].append({"type": "url_post", "label": label, "url": url, "as_button": as_button})

        def add_modax(
            self,
            label: str,
            modal_id: str,
            url: str,
            *,
            title: str = None,
            on_submit: str = None,
            on_redirect: str = None,
            primary: bool = False,
            as_button: bool = False,
            disabled: bool = False,
        ):
            self.groups[-1].append(
                {
                    "type": "modax",
                    "label": label,
                    "url": url,
                    "modal_id": modal_id,
                    "title": title or label,
                    "on_submit": on_submit,
                    "on_redirect": on_redirect,
                    "primary": primary,
                    "as_button": as_button,
                    "disabled": disabled,
                }
            )

        def as_items(self):
            """
            Reduce groups to a flat list of items separated by dividers.
            """
            items = []
            for group in self.groups:
                if not group:
                    continue
                if items:
                    items.append({"type": "divider"})
                items.extend(group)
            return items

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # does the page have a content menu?
        context["has_content_menu"] = len(self._get_content_menu()) > 0

        # does the page have a search query?
        if "search" in self.request.GET:
            context["has_search_query"] = urlencode({"search": self.request.GET["search"]})

        return context

    def _get_content_menu(self):
        menu = self.Menu()
        self.build_content_menu(menu)
        return menu.as_items()

    def build_content_menu(self, menu: Menu):  # pragma: no cover
        pass

    def get(self, request, *args, **kwargs):
        if "HTTP_TEMBA_CONTENT_MENU" in self.request.META:
            return JsonResponse({"items": self._get_content_menu()})

        return super().get(request, *args, **kwargs)
