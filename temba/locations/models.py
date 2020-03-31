import logging

import geojson
from mptt.models import MPTTModel, TreeForeignKey
from smartmin.models import SmartModel

from django.contrib.gis.db import models
from django.db.models import F, Value
from django.db.models.functions import Concat

logger = logging.getLogger(__name__)


# default manager for AdminBoundary, doesn't load geometries
class NoGeometryManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().defer("geometry", "simplified_geometry")


# optional 'geometries' manager for AdminBoundary, loads everything
class GeometryManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset()


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
    PATH_SEPARATOR = ">"
    PADDED_PATH_SEPARATOR = " > "

    osm_id = models.CharField(
        max_length=15, unique=True, help_text="This is the OSM id for this administrative boundary"
    )

    name = models.CharField(max_length=128, help_text="The name of our administrative boundary")

    level = models.IntegerField(
        help_text="The level of the boundary, 0 for country, 1 for state, 2 for district, 3 for ward"
    )

    parent = TreeForeignKey(
        "self",
        null=True,
        on_delete=models.PROTECT,
        blank=True,
        related_name="children",
        db_index=True,
        help_text="The parent to this political boundary if any",
    )

    path = models.CharField(max_length=768, help_text="The full path name for this location")

    geometry = models.MultiPolygonField(null=True, help_text="The full geometry of this administrative boundary")

    simplified_geometry = models.MultiPolygonField(
        null=True, help_text="The simplified geometry of this administrative boundary"
    )

    objects = NoGeometryManager()
    geometries = GeometryManager()

    @staticmethod
    def get_geojson_dump(name, features):
        # build a feature collection
        feature_collection = geojson.FeatureCollection(features)
        return geojson.dumps({"name": name, "geometry": feature_collection})

    def as_json(self):
        result = dict(osm_id=self.osm_id, name=self.name, level=self.level, aliases="", path=self.path)

        if self.parent:
            result["parent_osm_id"] = self.parent.osm_id

        aliases = "\n".join(sorted([alias.name for alias in self.aliases.all()]))
        result["aliases"] = aliases
        return result

    def get_geojson_feature(self):
        return geojson.Feature(
            properties=dict(name=self.name, osm_id=self.osm_id, id=self.pk, level=self.level),
            zoomable=True if self.children.all() else False,
            geometry=None if not self.simplified_geometry else geojson.loads(self.simplified_geometry.geojson),
        )

    def get_geojson(self):
        return AdminBoundary.get_geojson_dump(self.name, [self.get_geojson_feature()])

    def get_children_geojson(self):
        children = []
        for child in self.children.all():
            children.append(child.get_geojson_feature())
        return AdminBoundary.get_geojson_dump(self.name, children)

    def update(self, **kwargs):
        AdminBoundary.objects.filter(id=self.id).update(**kwargs)

        # update our object values so that self is up to date
        for key, value in kwargs.items():
            setattr(self, key, value)

    def update_path(self):
        if self.level == 0:
            self.path = self.name
            self.save(update_fields=("path",))

        def _update_child_paths(boundary):
            boundaries = AdminBoundary.objects.filter(parent=boundary).only("name", "parent__path")
            boundaries.update(
                path=Concat(Value(boundary.path), Value(" %s " % AdminBoundary.PATH_SEPARATOR), F("name"))
            )
            for boundary in boundaries:
                _update_child_paths(boundary)

        _update_child_paths(self)

    def release(self):
        for child_boundary in AdminBoundary.objects.filter(parent=self):  # pragma: no cover
            child_boundary.release()

        self.aliases.all().delete()
        self.delete()

    @classmethod
    def create(cls, osm_id, name, level, parent=None, **kwargs):
        """
        Create method that takes care of creating path based on name and parent
        """
        path = name
        if parent is not None:
            path = parent.path + AdminBoundary.PADDED_PATH_SEPARATOR + name

        return AdminBoundary.objects.create(osm_id=osm_id, name=name, level=level, parent=parent, path=path, **kwargs)

    @classmethod
    def strip_last_path(cls, path):
        """
        Strips the last part of the passed in path. Throws if there is no separator
        """
        parts = path.split(AdminBoundary.PADDED_PATH_SEPARATOR)
        if len(parts) <= 1:  # pragma: no cover
            raise Exception("strip_last_path called without a path to strip")

        return AdminBoundary.PADDED_PATH_SEPARATOR.join(parts[:-1])

    @classmethod
    def get_by_path(cls, org, path):
        cache = getattr(org, "_abs", {})

        if not cache:
            setattr(org, "_abs", cache)

        boundary = cache.get(path)
        if not boundary:
            boundary = AdminBoundary.objects.filter(path=path).first()
            cache[path] = boundary

        return boundary

    def __str__(self):
        return "%s" % self.name


class BoundaryAlias(SmartModel):
    """
    Alternative names for a boundaries
    """

    name = models.CharField(max_length=128, help_text="The name for our alias")

    boundary = models.ForeignKey(
        AdminBoundary,
        on_delete=models.PROTECT,
        help_text="The admin boundary this alias applies to",
        related_name="aliases",
    )

    org = models.ForeignKey("orgs.Org", on_delete=models.PROTECT, help_text="The org that owns this alias")

    @classmethod
    def create(cls, org, user, boundary, name):
        return cls.objects.create(org=org, boundary=boundary, name=name, created_by=user, modified_by=user)
