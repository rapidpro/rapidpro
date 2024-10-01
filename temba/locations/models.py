import geojson
from mptt.models import MPTTModel, TreeForeignKey
from smartmin.models import SmartModel

from django.contrib.gis.db import models
from django.db.models import F, Value
from django.db.models.functions import Concat, Upper


# default manager for AdminBoundary, doesn't load geometries
class NoGeometryManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().defer("simplified_geometry")


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

    MAX_NAME_LEN = 128

    # Used to separate segments in a hierarchy of boundaries. Has the advantage of being a character in GSM7 and
    # being very unlikely to show up in an admin boundary name.
    PATH_SEPARATOR = ">"
    PADDED_PATH_SEPARATOR = " > "

    osm_id = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=MAX_NAME_LEN)
    level = models.IntegerField()
    parent = TreeForeignKey("self", null=True, on_delete=models.PROTECT, related_name="children", db_index=True)
    path = models.CharField(max_length=768)  # e.g. Rwanda > Kigali
    simplified_geometry = models.MultiPolygonField(null=True)

    objects = NoGeometryManager()
    geometries = GeometryManager()

    @staticmethod
    def get_geojson_dump(name, features):
        # build a feature collection
        feature_collection = geojson.FeatureCollection(features)
        return geojson.dumps({"name": name, "geometry": feature_collection})

    def as_json(self, org):
        result = dict(osm_id=self.osm_id, name=self.name, level=self.level, aliases="", path=self.path)

        if self.parent:
            result["parent_osm_id"] = self.parent.osm_id

        aliases = "\n".join(sorted([alias.name for alias in self.aliases.filter(org=org)]))
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

    def update_aliases(self, org, user, aliases: list):
        siblings = self.parent.children.all()

        self.aliases.filter(org=org).delete()  # delete any existing aliases for this workspace

        for new_alias in aliases:
            assert new_alias and len(new_alias) < AdminBoundary.MAX_NAME_LEN

            # aliases are only allowed to exist on one boundary with same parent at a time
            BoundaryAlias.objects.filter(name=new_alias, boundary__in=siblings, org=org).delete()

            BoundaryAlias.create(org, user, self, new_alias)

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
        return self.name

    class Meta:
        indexes = [models.Index(Upper("name"), name="adminboundaries_by_name")]


class BoundaryAlias(SmartModel):
    """
    An org specific alias for a boundary name
    """

    org = models.ForeignKey("orgs.Org", on_delete=models.PROTECT)
    boundary = models.ForeignKey(AdminBoundary, on_delete=models.PROTECT, related_name="aliases")
    name = models.CharField(max_length=AdminBoundary.MAX_NAME_LEN, help_text="The name for our alias")

    @classmethod
    def create(cls, org, user, boundary, name):
        return cls.objects.create(org=org, boundary=boundary, name=name, created_by=user, modified_by=user)

    class Meta:
        indexes = [models.Index(Upper("name"), name="boundaryaliases_by_name")]
