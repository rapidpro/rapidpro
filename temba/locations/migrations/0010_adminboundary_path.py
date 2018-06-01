from django.db import migrations, models
from django.db.models import F, Value
from django.db.models.functions import Concat


def apply_as_migration(apps, schema_editor):
    AdminBoundary = apps.get_model("locations", "AdminBoundary")

    for country in AdminBoundary.objects.filter(level=0):
        country.path = country.name
        country.save(update_fields=("path",))

        def update_paths(boundary):
            print(" ** Updating path for %s" % boundary.name)
            boundaries = AdminBoundary.objects.filter(parent=boundary).only("name", "parent__path")
            boundaries.update(path=Concat(Value(boundary.path), Value(" > "), F("name")))
            for boundary in boundaries:
                update_paths(boundary)

        update_paths(country)


def undo_path(apps, schema_editor):
    AdminBoundary = apps.get_model("locations", "AdminBoundary")
    AdminBoundary.objects.all().update(path=None)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("locations", "0009_auto_20170228_0837")]

    operations = [
        migrations.AddField(
            model_name="adminboundary",
            name="path",
            field=models.CharField(help_text="The full path name for this location", max_length=768, null=True),
        ),
        migrations.RunPython(apply_as_migration, undo_path),
    ]
