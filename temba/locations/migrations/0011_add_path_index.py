from django.db import migrations
from django.db.models.functions import Concat
from django.db.models import Value, F


def populate_paths(apps, schema_editor):
    AdminBoundary = apps.get_model("locations", "AdminBoundary")

    def update_child_paths(boundary):
        print(" ** Updating path for %s" % boundary.name)
        boundaries = AdminBoundary.objects.filter(parent=boundary).only("name", "parent__path")
        boundaries.update(path=Concat(Value(boundary.path), Value(" > "), F("name")))
        for boundary in boundaries:
            update_child_paths(boundary)

    # populate any countries and their children
    for country in AdminBoundary.objects.filter(level=0, path=None):
        country.path = country.name
        country.save(update_fields=("path",))
        update_child_paths(country)

    # populate any other boundaries missing paths
    for missing_path in AdminBoundary.objects.filter(path=None).select_related("parent"):
        if missing_path.parent.path:
            missing_path.path = missing_path.parent.path + " > " + missing_path.name
            missing_path.save(update_fields=["path"])
            update_child_paths(missing_path)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    atomic = False

    CREATE_INDEX_SQL = """
    CREATE UNIQUE INDEX CONCURRENTLY locations_adminboundary_upper_path ON locations_adminboundary(UPPER(path));
    """

    DROP_INDEX_SQL = """
    DROP INDEX CONCURRENTLY locations_adminboundary_upper_path;
    """

    dependencies = [("locations", "0010_adminboundary_path")]

    operations = [migrations.RunPython(populate_paths, noop), migrations.RunSQL(CREATE_INDEX_SQL, DROP_INDEX_SQL)]
