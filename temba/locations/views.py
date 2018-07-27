# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json

from django.contrib import messages
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from smartmin.views import SmartCRUDL, SmartReadView, SmartUpdateView
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.orgs.views import OrgPermsMixin


class BoundaryCRUDL(SmartCRUDL):
    actions = ('alias', 'geometry', 'boundaries')
    model = AdminBoundary

    class Alias(OrgPermsMixin, SmartReadView):

        @classmethod
        def derive_url_pattern(cls, path, action):
            # though we are a read view, we don't actually need an id passed
            # in, that is derived
            return r'^%s/%s/$' % (path, action)

        def pre_process(self, request, *args, **kwargs):
            response = super(BoundaryCRUDL.Alias, self).pre_process(
                self, request, *args, **kwargs)

            # we didn't shortcut for some other reason, check that they have an
            # org
            if not response:
                org = request.user.get_org()
                if not org.country:
                    messages.warning(request, _("You must select a country for your organization."))
                    return HttpResponseRedirect(reverse('orgs.org_home'))

            return None

        def get_object(self, queryset=None):
            org = self.request.user.get_org()
            return org.country

    class Geometry(OrgPermsMixin, SmartReadView):

        @classmethod
        def derive_url_pattern(cls, path, action):
            # though we are a read view, we don't actually need an id passed
            # in, that is derived
            return r'^%s/%s/(?P<osmId>\w\d+)/$' % (path, action)

        def get_object(self):
            return AdminBoundary.geometries.get(osm_id=self.kwargs['osmId'])

        def render_to_response(self, context):
            if self.object.children.all().count() > 0:
                return HttpResponse(self.object.get_children_geojson(), content_type='application/json')
            return HttpResponse(self.object.get_geojson(), content_type='application/json')

    class Boundaries(OrgPermsMixin, SmartUpdateView):

        @csrf_exempt
        def dispatch(self, *args, **kwargs):
            return super(BoundaryCRUDL.Boundaries, self).dispatch(*args, **kwargs)

        @classmethod
        def derive_url_pattern(cls, path, action):
            # though we are a read view, we don't actually need an id passed
            # in, that is derived
            return r'^%s/%s/(?P<osmId>\w\d+)/$' % (path, action)

        def get_object(self):
            return AdminBoundary.geometries.get(osm_id=self.kwargs['osmId'])

        def post(self, request, *args, **kwargs):

            def update_boundary_aliases(boundary):
                level_boundary = AdminBoundary.objects.filter(osm_id=boundary['osm_id']).first()
                if level_boundary:
                    boundary_aliases = boundary.get('aliases', '')
                    update_aliases(level_boundary, boundary_aliases)

            def update_aliases(boundary, new_aliases):
                # for now, nuke and recreate all aliases
                BoundaryAlias.objects.filter(boundary=boundary, org=org).delete()
                for new_alias in new_aliases.split('\n'):
                    if new_alias:
                        BoundaryAlias.objects.create(boundary=boundary, org=org, name=new_alias.strip(),
                                                     created_by=self.request.user, modified_by=self.request.user)

            # try to parse our body
            json_string = request.body
            org = request.user.get_org()

            try:
                json_list = json.loads(json_string)
            except Exception as e:
                return JsonResponse(dict(status="error", description="Error parsing JSON: %s" % str(e)), status=400)

            # this can definitely be optimized
            for state in json_list:
                state_boundary = AdminBoundary.objects.filter(osm_id=state['osm_id']).first()
                state_aliases = state.get('aliases', '')
                if state_boundary:
                    update_aliases(state_boundary, state_aliases)
                    if 'children' in state:
                        for district in state['children']:
                            update_boundary_aliases(district)
                            if 'children' in district:
                                for ward in district['children']:
                                    update_boundary_aliases(ward)

            return JsonResponse(json_list, safe=False)

        def get(self, request, *args, **kwargs):
            tops = list(AdminBoundary.geometries.filter(parent__osm_id=self.get_object().osm_id).order_by('name'))

            tops_children = AdminBoundary.geometries.filter(
                Q(parent__osm_id__in=[boundary.osm_id for boundary in tops])).order_by('parent__osm_id', 'name')

            boundaries = [top.as_json() for top in tops]

            current_top = None
            match = ''
            for child in tops_children:
                child = child.as_json()
                # find the appropriate top if necessary
                if not current_top or current_top['osm_id'] != child['parent_osm_id']:
                    for top in boundaries:
                        if top['osm_id'] == child['parent_osm_id']:
                            current_top = top
                            match = '%s %s' % (current_top['name'], current_top['aliases'])
                            current_top['match'] = match

                children = current_top.get('children', [])
                child['match'] = '%s %s' % (child['name'], child['aliases'])

                child_children = list(AdminBoundary.geometries.filter(Q(parent__osm_id=child['osm_id'])).order_by('name'))
                sub_children = child.get('children', [])
                for sub_child in child_children:
                    sub_child = sub_child.as_json()
                    sub_child['match'] = '%s %s %s %s %s' % (sub_child['name'], sub_child[
                                                             'aliases'], child['name'], child['aliases'], match)

                    sub_children.append(sub_child)
                    child['match'] = '%s %s %s' % (child['match'], sub_child['name'], sub_child['aliases'])

                child['children'] = sub_children
                children.append(child)
                current_top['children'] = children
                current_top['match'] = '%s %s' % (current_top['match'], child['match'])

            return JsonResponse(boundaries, safe=False)
