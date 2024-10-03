from smartmin.views import SmartListView, SmartTemplateView

from django.http import JsonResponse
from django.urls import reverse
from django.utils.text import slugify

from .mixins import OrgPermsMixin


class BaseListView(OrgPermsMixin, SmartListView):
    """
    Base list view for objects that belong to the current org
    """

    def derive_queryset(self, **kwargs):
        queryset = super().derive_queryset(**kwargs)

        if not self.request.user.is_authenticated:
            return queryset.none()  # pragma: no cover
        else:
            return queryset.filter(org=self.request.org)


class BaseMenuView(OrgPermsMixin, SmartTemplateView):
    """
    Base view for the section menus
    """

    def create_divider(self):
        return {"type": "divider"}

    def create_space(self):  # pragma: no cover
        return {"type": "space"}

    def create_section(self, name, items=()):  # pragma: no cover
        return {"id": slugify(name), "name": name, "type": "section", "items": items}

    def create_list(self, name, href, type):
        return {"id": name, "href": href, "type": type}

    def create_modax_button(self, name, href, icon=None, on_submit=None):  # pragma: no cover
        menu_item = {"id": slugify(name), "name": name, "type": "modax-button"}
        if href:
            if href[0] == "/":  # pragma: no cover
                menu_item["href"] = href
            elif self.has_org_perm(href):
                menu_item["href"] = reverse(href)

        if on_submit:
            menu_item["on_submit"] = on_submit

        if icon:  # pragma: no cover
            menu_item["icon"] = icon

        if "href" not in menu_item:  # pragma: no cover
            return None

        return menu_item

    def create_menu_item(
        self,
        menu_id=None,
        name=None,
        icon=None,
        avatar=None,
        endpoint=None,
        href=None,
        count=None,
        perm=None,
        items=[],
        inline=False,
        bottom=False,
        popup=False,
        event=None,
        posterize=False,
        bubble=None,
        mobile=False,
    ):
        if perm and not self.has_org_perm(perm):  # pragma: no cover
            return

        menu_item = {"name": name, "inline": inline}
        menu_item["id"] = menu_id if menu_id else slugify(name)
        menu_item["bottom"] = bottom
        menu_item["popup"] = popup
        menu_item["avatar"] = avatar
        menu_item["posterize"] = posterize
        menu_item["event"] = event
        menu_item["mobile"] = mobile

        if bubble:
            menu_item["bubble"] = bubble

        if icon:
            menu_item["icon"] = icon

        if count is not None:
            menu_item["count"] = count

        if endpoint:
            if endpoint[0] == "/":  # pragma: no cover
                menu_item["endpoint"] = endpoint
            elif perm or self.has_org_perm(endpoint):
                menu_item["endpoint"] = reverse(endpoint)

        if href:
            if href[0] == "/":
                menu_item["href"] = href
            elif perm or self.has_org_perm(href):
                menu_item["href"] = reverse(href)

        if items:  # pragma: no cover
            menu_item["items"] = [item for item in items if item is not None]

        # only include the menu item if we have somewhere to go
        if "href" not in menu_item and "endpoint" not in menu_item and not inline and not popup and not event:
            return None

        return menu_item

    def get_menu(self):
        return [item for item in self.derive_menu() if item is not None]

    def render_to_response(self, context, **response_kwargs):
        return JsonResponse({"results": self.get_menu()})
