from optparse import make_option
import os
import regex
from zipfile import ZipFile
from django.contrib.gis.geos import Polygon, MultiPolygon
from django.core.management.base import BaseCommand, CommandError
from temba.locations.models import AdminBoundary, COUNTRY_LEVEL, STATE_LEVEL, DISTRICT_LEVEL
import geojson


class Command(BaseCommand):  # pragma: no cover
    option_list = BaseCommand.option_list + (
        make_option('--country', '-c', dest='country', default=None,
                    help="Only process the boundary files for this country osm id."),
    )
    args = '<file1.zip | 49915admin1.json.. >'
    help = 'Import our geojson zip file format, updating all our OSM data accordingly.'

    def import_file(self, filename, file):
        admin_json = geojson.loads(file.read())

        # we keep track of all the osm ids we've seen because we remove all admin levels at this level
        # which weren't seen. (they have been removed)
        seen_osm_ids = []

        # track currently processed admin boundar
        current_boundary = None

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
                print "Skipping '%s', doesn't match file pattern." % filename

        # for each of our features
        for feature in admin_json['features']:
            # what level are we?
            props = feature.properties
            # get parent id which is set in new file format
            parent_osm_id = props.get('parent_id')

            # if parent_osm_id is not set and not COUNTRY_LEVEL check for old
            # file format
            if not parent_osm_id and level != COUNTRY_LEVEL:
                if level == STATE_LEVEL:
                    parent_osm_id = props['is_in_country']
                elif level == DISTRICT_LEVEL:
                    parent_osm_id = props['is_in_state']

            osm_id = props['osm_id']
            name = props.get('name_en', '')
            if not name or name == 'None':
                name = props['name']

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

            kwargs = dict(osm_id=osm_id, name=name, level=level, parent=parent)
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
        # TODO: how do we deal with values already assigned to a location? we should probably retry to do some
        # matching based on the new names? (though unlikely to match if the
        # name didn't match when trying to find the boundary)
        current_boundary = AdminBoundary.objects.filter(osm_id=osm_id).first()
        if current_boundary:
            country = current_boundary.get_root()
            country.get_descendants().filter(level=level).exclude(
                osm_id__in=seen_osm_ids).delete()

    def handle(self, *args, **options):
        filenames = []

        zipfile = None
        if args[0].endswith(".zip"):
            zipfile = ZipFile(args[0], 'r')
            filenames = zipfile.namelist()

        else:
            filenames = list(args)

        # are we filtering by a prefix?
        prefix = ''
        if options['country']:
            prefix = '%sadmin' % options['country']

        # sort our filenames, this will make sure we import 0 levels before 1
        # before 2
        filenames.sort()

        # for each file they have given us
        for filename in filenames:
            # if it ends in json, then it is geojson, try to parse it
            if filename.startswith(prefix) and filename.endswith('json'):
                # read the file entirely
                print "=== parsing %s" % filename

                # if we are reading from a zipfile, read it from there
                if zipfile:
                    with zipfile.open(filename) as json_file:
                        self.import_file(filename, json_file)

                # otherwise, straight off the filesystem
                else:
                    with open(filename) as json_file:
                        self.import_file(filename, json_file)
