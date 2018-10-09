import mptt.fields

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [("locations", "0006_reset_1"), ("orgs", "0029_reset_1")]

    operations = [
        migrations.AddField(
            model_name="boundaryalias",
            name="org",
            field=models.ForeignKey(
                help_text="The org that owns this alias", on_delete=django.db.models.deletion.CASCADE, to="orgs.Org"
            ),
        ),
        migrations.AddField(
            model_name="adminboundary",
            name="parent",
            field=mptt.fields.TreeForeignKey(
                blank=True,
                help_text="The parent to this political boundary if any",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="children",
                to="locations.AdminBoundary",
            ),
        ),
    ]
