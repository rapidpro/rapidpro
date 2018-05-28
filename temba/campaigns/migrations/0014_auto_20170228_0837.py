import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("campaigns", "0013_reset_4")]

    operations = [
        migrations.AlterField(
            model_name="campaign",
            name="created_on",
            field=models.DateTimeField(
                blank=True,
                default=django.utils.timezone.now,
                editable=False,
                help_text="When this item was originally created",
            ),
        ),
        migrations.AlterField(
            model_name="campaign",
            name="modified_on",
            field=models.DateTimeField(
                blank=True,
                default=django.utils.timezone.now,
                editable=False,
                help_text="When this item was last modified",
            ),
        ),
        migrations.AlterField(
            model_name="campaignevent",
            name="created_on",
            field=models.DateTimeField(
                blank=True,
                default=django.utils.timezone.now,
                editable=False,
                help_text="When this item was originally created",
            ),
        ),
        migrations.AlterField(
            model_name="campaignevent",
            name="modified_on",
            field=models.DateTimeField(
                blank=True,
                default=django.utils.timezone.now,
                editable=False,
                help_text="When this item was last modified",
            ),
        ),
    ]
