from optparse import make_option
import os
import regex
from zipfile import ZipFile
from django.contrib.gis.geos import Polygon, MultiPolygon
from django.core.management.base import BaseCommand, CommandError
from temba.locations.models import AdminBoundary, COUNTRY_LEVEL
import geojson


class Command(BaseCommand):  # pragma: no cover
    option_list = BaseCommand.option_list + (
        make_option('--country', '-c', dest='country', default=None,
                    help="Only process the boundary files for this country osm id."),
    )
    args = '<file1.zip | 49915admin1.json.. >'
    help = 'Import our geojson zip file format, updating all our OSM data accordingly.'

    def get_country_id(self, props):
        if props.get('admin_level') is COUNTRY_LEVEL:
            return props.get('osm_id')
        return props.get('is_in_country')

    def import_file(self, file_obj, country_id):
        admin_json = geojson.loads(file_obj.read())

        # we keep track of all the osm ids we've seen because we remove all admin levels at this level
        # which weren't seen. (they have been removed)
        seen_osm_ids = []

        country_osm_id = None
        level = None

        # for each of our features
        for feature in admin_json['features']:
            # what level are we?
            props = feature.properties
            country_osm_id = props.get('is_in_country')
            level = props.get('admin_level')
            is_simplified = props.get('is_simplified')
            parent_osm_id = props.get('parent_id')
            osm_id = props['osm_id']
            name = props.get('name_en', '')
            if not name or name == 'None':
                name = props['name']

            # Skip feature if import is country specific and does not belong to the
            # country we are currently importing
            if country_id and country_id != str(self.get_country_id(props)):
                print("Skipping %s because it does not match country OSM id: %s" %
                      (name, country_osm_id))
                continue

            # Try to get country id if admin level is not a country else bail
            if country_osm_id is None and level is not COUNTRY_LEVEL:
                print("Skipping %s (%s) as country id is not defined." %
                      (name, osm_id))
                continue

            # try to find parent, bail if we can't
            parent = None
            if parent_osm_id and parent_osm_id != 'None':
                parent = AdminBoundary.objects.filter(
                    osm_id=parent_osm_id).first()
                if not parent:
                    print("Skipping %s (%s) as parent %s not found." %
                          (name, osm_id, parent_osm_id))
                    continue

            # try to find existing admin level by osm_id
            boundary = AdminBoundary.objects.filter(osm_id=osm_id)

            # didn't find it? what about by name?
            if not boundary:
                boundary = AdminBoundary.objects.filter(
                    parent=parent, name__iexact=name)

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

            kwargs = dict(osm_id=osm_id, name=name, level=level,
                          parent=parent, in_country=country_osm_id)
            if is_simplified:
                kwargs['simplified_geometry'] = geometry
            else:
                kwargs['geometry'] = geometry

            # if this is an update, just update with those fields
            if boundary:
                print " ** updating %s (%s)" % (name, osm_id)
                boundary.update(**kwargs)

            # otherwise, this is new, so create it
            else:
                print " ** adding %s (%s)" % (name, osm_id)
                AdminBoundary.objects.create(**kwargs)

            # keep track of this osm_id
            seen_osm_ids.append(osm_id)

        # now remove any unseen boundaries
        # matching based on the new names? (though unlikely to match if the
        # name didn't match when trying to find the boundary)
        AdminBoundary.objects.filter(level=level, in_country=country_osm_id).exclude(
            osm_id__in=seen_osm_ids).delete()

    def handle(self, *args, **options):
        filenames = []

        zipfile = None
        if args[0].endswith(".zip"):
            zipfile = ZipFile(args[0], 'r')
            filenames = zipfile.namelist()

        else:
            filenames = list(args)

        # are we importing for a specific country?
        country_osm_id = options.get('country')

        # sort our filenames, this will make sure we import 0 levels before 1
        # before 2
        filenames.sort()

        # for each file they have given us
        for filename in filenames:
            # if it ends in json, then it is geojson, try to parse it
            if filename.endswith('json'):
                # read the file entirely
                print "=== parsing %s" % filename

                # if we are reading from a zipfile, read it from there
                if zipfile:
                    with zipfile.open(filename) as json_file:
                        self.import_file(json_file, country_osm_id)

                # otherwise, straight off the filesystem
                else:
                    with open(filename) as json_file:
                        self.import_file(json_file, country_osm_id)
