# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import geojson
import logging
import six

from django.contrib.gis.db import models
from mptt.models import MPTTModel, TreeForeignKey
from smartmin.models import SmartModel

logger = logging.getLogger(__name__)


# default manager for AdminBoundary, doesn't load geometries
class NoGeometryManager(models.GeoManager):
    def get_queryset(self):
        return super(NoGeometryManager, self).get_queryset().defer('geometry', 'simplified_geometry')


# optional 'geometries' manager for AdminBoundary, loads everything
class GeometryManager(models.GeoManager):
    def get_queryset(self):
        return super(GeometryManager, self).get_queryset()


@six.python_2_unicode_compatible
class AdminBoundary(MPTTModel, models.Model):
    """
    Represents a single administrative boundary (like a country, state or district)
    """
    LEVEL_COUNTRY = 0
    LEVEL_STATE = 1
    LEVEL_DISTRICT = 2
    LEVEL_WARD = 3

    # used to separate segments in a hierarchy of boundaries. Has the advantage of being a character in GSM7 and
    # being very unlikely to show up in an admin boundary name.
    PATH_SEPARATOR = '>'

    osm_id = models.CharField(max_length=15, unique=True,
                              help_text="This is the OSM id for this administrative boundary")

    name = models.CharField(max_length=128,
                            help_text="The name of our administrative boundary")

    level = models.IntegerField(help_text="The level of the boundary, 0 for country, 1 for state, 2 for district, 3 for ward")

    parent = TreeForeignKey('self', null=True, blank=True, related_name='children', db_index=True,
                            help_text="The parent to this political boundary if any")

    geometry = models.MultiPolygonField(null=True,
                                        help_text="The full geometry of this administrative boundary")

    simplified_geometry = models.MultiPolygonField(null=True,
                                                   help_text="The simplified geometry of this administrative boundary")

    objects = NoGeometryManager()
    geometries = GeometryManager()

    @staticmethod
    def get_geojson_dump(features):
        # build a feature collection
        feature_collection = geojson.FeatureCollection(features)
        return geojson.dumps(feature_collection)

    def as_json(self):
        result = dict(osm_id=self.osm_id, name=self.name,
                      level=self.level, aliases='')

        if self.parent:
            result['parent_osm_id'] = self.parent.osm_id

        aliases = '\n'.join([alias.name for alias in self.aliases.all()])
        result['aliases'] = aliases
        return result

    def get_geojson_feature(self):
        return geojson.Feature(properties=dict(name=self.name, osm_id=self.osm_id, id=self.pk, level=self.level),
                               zoomable=True if self.children.all() else False,
                               geometry=None if not self.simplified_geometry else geojson.loads(self.simplified_geometry.geojson))

    def get_geojson(self):
        return AdminBoundary.get_geojson_dump([self.get_geojson_feature()])

    def get_children_geojson(self):
        children = []
        for child in self.children.all():
            children.append(child.get_geojson_feature())
        return AdminBoundary.get_geojson_dump(children)

    def as_path(self):
        """
        Returns the full path for this admin boundary, from country downwards using > as separator between
        each level.
        """
        if self.parent:
            return "%s %s %s" % (self.parent.as_path(), AdminBoundary.PATH_SEPARATOR, self.name.replace(AdminBoundary.PATH_SEPARATOR, " "))
        else:
            return self.name.replace(AdminBoundary.PATH_SEPARATOR, " ")

    def update(self, **kwargs):
        AdminBoundary.objects.filter(id=self.id).update(**kwargs)

        # if our name changed, update the category on any of our values
        name = kwargs.get('name', self.name)
        if name != self.name:
            from temba.values.models import Value
            Value.objects.filter(location_value=self).update(category=name)

        # update our object values so that self is up to date
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __str__(self):
        return "%s" % self.name


class BoundaryAlias(SmartModel):
    """
    Alternative names for a boundaries
    """
    name = models.CharField(max_length=128, help_text="The name for our alias")

    boundary = models.ForeignKey(AdminBoundary, help_text='The admin boundary this alias applies to', related_name='aliases')

    org = models.ForeignKey('orgs.Org', help_text="The org that owns this alias")

    @classmethod
    def create(cls, org, user, boundary, name):
        return cls.objects.create(org=org, boundary=boundary, name=name, created_by=user, modified_by=user)
