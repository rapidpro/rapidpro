import django.contrib.gis.db.models.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="AdminBoundary",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "osm_id",
                    models.CharField(
                        help_text="This is the OSM id for this administrative boundary", max_length=15, unique=True
                    ),
                ),
                ("name", models.CharField(help_text="The name of our administrative boundary", max_length=128)),
                (
                    "level",
                    models.IntegerField(
                        help_text="The level of the boundary, 0 for country, 1 for state, 2 for district, 3 for ward"
                    ),
                ),
                (
                    "geometry",
                    django.contrib.gis.db.models.fields.MultiPolygonField(
                        help_text="The full geometry of this administrative boundary", null=True, srid=4326
                    ),
                ),
                (
                    "simplified_geometry",
                    django.contrib.gis.db.models.fields.MultiPolygonField(
                        help_text="The simplified geometry of this administrative boundary", null=True, srid=4326
                    ),
                ),
                ("lft", models.PositiveIntegerField(db_index=True, editable=False)),
                ("rght", models.PositiveIntegerField(db_index=True, editable=False)),
                ("tree_id", models.PositiveIntegerField(db_index=True, editable=False)),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="BoundaryAlias",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "is_active",
                    models.BooleanField(
                        default=True, help_text="Whether this item is active, use this instead of deleting"
                    ),
                ),
                (
                    "created_on",
                    models.DateTimeField(auto_now_add=True, help_text="When this item was originally created"),
                ),
                ("modified_on", models.DateTimeField(auto_now=True, help_text="When this item was last modified")),
                ("name", models.CharField(help_text="The name for our alias", max_length=128)),
                (
                    "boundary",
                    models.ForeignKey(
                        help_text="The admin boundary this alias applies to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="aliases",
                        to="locations.AdminBoundary",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user which originally created this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="locations_boundaryalias_creations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        help_text="The user which last modified this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="locations_boundaryalias_modifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
    ]
