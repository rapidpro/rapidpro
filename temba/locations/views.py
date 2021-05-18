from smartmin.views import SmartCRUDL, SmartReadView, SmartUpdateView

from django.contrib import messages
from django.db.models import Prefetch
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.orgs.views import OrgPermsMixin
from temba.utils import json


class BoundaryCRUDL(SmartCRUDL):
    actions = ("alias", "geometry", "boundaries")
    model = AdminBoundary

    class Alias(OrgPermsMixin, SmartReadView):
        @classmethod
        def derive_url_pattern(cls, path, action):
            # though we are a read view, we don't actually need an id passed
            # in, that is derived
            return r"^%s/%s/$" % (path, action)

        def get_gear_links(self):
            return [dict(title=_("Home"), style="button-light", href=reverse("orgs.org_home"),)]

        def pre_process(self, request, *args, **kwargs):
            response = super().pre_process(self, request, *args, **kwargs)

            # we didn't shortcut for some other reason, check that they have an
            # org
            if not response:
                org = request.user.get_org()
                if not org.country:
                    messages.warning(request, _("You must select a country for your workspace."))
                    return HttpResponseRedirect(reverse("orgs.org_home"))

            return None

        def get_object(self, queryset=None):
            org = self.request.user.get_org()
            return org.country

    class Geometry(OrgPermsMixin, SmartReadView):
        @classmethod
        def derive_url_pattern(cls, path, action):
            # though we are a read view, we don't actually need an id passed
            # in, that is derived
            return r"^%s/%s/(?P<osmId>\w+\.?\d+\.?\d?\_?\d?)/$" % (path, action)

        def get_object(self):
            return AdminBoundary.geometries.get(osm_id=self.kwargs["osmId"])

        def render_to_response(self, context):
            if self.object.children.all().count() > 0:
                return HttpResponse(self.object.get_children_geojson(), content_type="application/json")
            return HttpResponse(self.object.get_geojson(), content_type="application/json")

    class Boundaries(OrgPermsMixin, SmartUpdateView):
        @csrf_exempt
        def dispatch(self, *args, **kwargs):
            return super().dispatch(*args, **kwargs)

        @classmethod
        def derive_url_pattern(cls, path, action):
            # though we are a read view, we don't actually need an id passed
            # in, that is derived
            return r"^%s/%s/(?P<osmId>[\w\.]+)/$" % (path, action)

        def get_object(self):
            return AdminBoundary.geometries.get(osm_id=self.kwargs["osmId"])

        def post(self, request, *args, **kwargs):
            def update_aliases(boundary, new_aliases):
                # for now, nuke and recreate all aliases
                BoundaryAlias.objects.filter(boundary=boundary, org=org).delete()
                unique_new_aliases = list(set(new_aliases.split("\n")))
                for new_alias in unique_new_aliases:
                    if new_alias:
                        new_alias = new_alias.strip()

                        # aliases are only allowed to exist on one boundary at a time
                        BoundaryAlias.objects.filter(name=new_alias, org=org).delete()

                        BoundaryAlias.objects.create(
                            boundary=boundary,
                            org=org,
                            name=new_alias,
                            created_by=self.request.user,
                            modified_by=self.request.user,
                        )

            # try to parse our body
            json_string = request.body
            org = request.user.get_org()

            try:
                boundary_update = json.loads(json_string)
            except Exception as e:
                return JsonResponse(dict(status="error", description="Error parsing JSON: %s" % str(e)), status=400)

            boundary = AdminBoundary.objects.filter(osm_id=boundary_update["osm_id"]).first()
            aliases = boundary_update.get("aliases", "")
            if boundary:
                update_aliases(boundary, aliases)

            return JsonResponse(boundary_update, safe=False)

        def get(self, request, *args, **kwargs):
            org = request.user.get_org()
            boundary = self.get_object()

            page_size = 25

            # searches just return a list of all matches
            query = request.GET.get("q", None)
            if query:
                page = int(request.GET.get("page", 0))
                matches = set(
                    AdminBoundary.objects.filter(
                        path__startswith=f"{boundary.name} {AdminBoundary.PATH_SEPARATOR}"
                    ).filter(name__icontains=query)
                )
                aliases = BoundaryAlias.objects.filter(name__icontains=query, org=org)
                for alias in aliases:
                    matches.add(alias.boundary)

                start = page * page_size
                end = start + page_size

                matches = sorted(matches, key=lambda match: match.name)[start:end]
                response = [match.as_json(org) for match in matches]
                return JsonResponse(response, safe=False)

            # otherwise grab each item in the path
            path = []
            while boundary:
                children = list(
                    AdminBoundary.objects.filter(parent__osm_id=boundary.osm_id)
                    .order_by("name")
                    .prefetch_related(
                        Prefetch("aliases", queryset=BoundaryAlias.objects.filter(org=org).order_by("name"))
                    )
                )

                item = boundary.as_json(org)
                children_json = []
                for child in children:
                    child_json = child.as_json(org)
                    child_json["has_children"] = AdminBoundary.objects.filter(parent__osm_id=child.osm_id).exists()
                    children_json.append(child_json)

                item["children"] = children_json
                item["has_children"] = len(children_json) > 0
                path.append(item)
                boundary = boundary.parent

            path.reverse()
            return JsonResponse(path, safe=False)
