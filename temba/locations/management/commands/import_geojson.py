# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import os.path
import geojson
import regex

from zipfile import ZipFile
from django.contrib.gis.geos import Polygon, MultiPolygon
from django.core.management.base import BaseCommand
from temba.locations.models import AdminBoundary


class Command(BaseCommand):  # pragma: no cover
    help = 'Import our geojson zip file format, updating all our OSM data accordingly.'

    def add_arguments(self, parser):
        parser.add_argument('files', nargs='+')
        parser.add_argument('--country',
                            dest='country',
                            default=None,
                            help="Only process the boundary files for this country osm id")

    def import_file(self, filename, file):
        admin_json = geojson.loads(file.read())

        # we keep track of all the osm ids we've seen because we remove all admin levels at this level
        # which weren't seen. (they have been removed)
        seen_osm_ids = []
        osm_id = None

        # parse our filename.. they are in the format:
        # 192787admin2_simplified.json
        match = regex.match(
            r'(\w\d+)admin(\d)(_simplified)?\.json$', filename, regex.V0)
        level = None
        is_simplified = None
        if match:
            level = int(match.group(2))
            is_simplified = True if match.group(3) else False
        else:
            # else parse other filenames that are in
            # admin_level_0_simplified.json format.
            match = regex.match(
                r'admin_level_(\d)(_simplified)?\.json$', filename, regex.V0)
            if match:
                level = int(match.group(1))
                is_simplified = True if match.group(2) else False
            elif not match:
                print("Skipping '%s', doesn't match file pattern." % filename)
                return

        # for each of our features
        for feature in admin_json['features']:
            # what level are we?
            props = feature.properties

            # get parent id which is set in new file format
            parent_osm_id = props.get('parent_id')

            # if parent_osm_id is not set and not LEVEL_COUNTRY check for old file format
            if not parent_osm_id and level != AdminBoundary.LEVEL_COUNTRY:
                if level == AdminBoundary.LEVEL_STATE:
                    parent_osm_id = props['is_in_country']
                elif level == AdminBoundary.LEVEL_DISTRICT:
                    parent_osm_id = props['is_in_state']

            osm_id = props['osm_id']
            name = props.get('name', '')
            if not name or name == 'None' or level == AdminBoundary.LEVEL_COUNTRY:
                name = props.get('name_en', '')

            # try to find parent, bail if we can't
            parent = None
            if parent_osm_id and parent_osm_id != 'None':
                parent = AdminBoundary.objects.filter(osm_id=parent_osm_id).first()
                if not parent:
                    print("Skipping %s (%s) as parent %s not found." %
                          (name, osm_id, parent_osm_id))
                    continue

            # try to find existing admin level by osm_id
            boundary = AdminBoundary.objects.filter(osm_id=osm_id)

            # didn't find it? what about by name?
            if not boundary:
                boundary = AdminBoundary.objects.filter(parent=parent, name__iexact=name)

            # skip over items with no geometry
            if not feature['geometry'] or not feature['geometry']['coordinates']:
                continue

            polygons = []
            if feature['geometry']['type'] == 'Polygon':
                polygons.append(Polygon(*feature['geometry']['coordinates']))
            elif feature['geometry']['type'] == 'MultiPolygon':
                for polygon in feature['geometry']['coordinates']:
                    polygons.append(Polygon(*polygon))
            else:
                raise Exception("Error importing %s, unknown geometry type '%s'" % (
                    name, feature['geometry']['type']))

            geometry = MultiPolygon(polygons)

            kwargs = dict(osm_id=osm_id, name=name, level=level, parent=parent)
            if is_simplified:
                kwargs['simplified_geometry'] = geometry
            else:
                kwargs['geometry'] = geometry

            # if this is an update, just update with those fields
            if boundary:
                print(" ** updating %s (%s)" % (name, osm_id))
                boundary = boundary.first()
                boundary.update(**kwargs)

            # otherwise, this is new, so create it
            else:
                print(" ** adding %s (%s)" % (name, osm_id))
                AdminBoundary.objects.create(**kwargs)

            # keep track of this osm_id
            seen_osm_ids.append(osm_id)

        # now remove any unseen boundaries
        if osm_id:
            last_boundary = AdminBoundary.objects.filter(osm_id=osm_id).first()
            if last_boundary:
                print(" ** removing unseen boundaries (%s)" % (osm_id))
                country = last_boundary.get_root()
                country.get_descendants().filter(level=level).exclude(osm_id__in=seen_osm_ids).delete()
                return country

    def handle(self, *args, **options):
        files = options['files']

        zipfile = None
        if files[0].endswith(".zip"):
            zipfile = ZipFile(files[0], 'r')
            filepaths = zipfile.namelist()

        else:
            filepaths = list(files)

        # are we filtering by a prefix?
        prefix = ''
        if options['country']:
            prefix = '%sadmin' % options['country']

        # sort our filepaths, this will make sure we import 0 levels before 1
        # before 2
        filepaths.sort()

        country = None
        # for each file they have given us
        for filepath in filepaths:
            filename = os.path.basename(filepath)
            # if it ends in json, then it is geojson, try to parse it
            if filename.startswith(prefix) and filename.endswith('json'):
                # read the file entirely
                print("=== parsing %s" % filename)

                # if we are reading from a zipfile, read it from there
                if zipfile:
                    with zipfile.open(filepath) as json_file:
                        country = self.import_file(filename, json_file)

                # otherwise, straight off the filesystem
                else:
                    with open(filepath) as json_file:
                        country = self.import_file(filename, json_file)

        if country:
            print(" ** updating paths for all of %s" % country.name)
            country.update_path()
