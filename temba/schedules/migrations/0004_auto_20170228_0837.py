import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("schedules", "0003_reset_1")]

    operations = [
        migrations.AlterField(
            model_name="schedule",
            name="created_on",
            field=models.DateTimeField(
                blank=True,
                default=django.utils.timezone.now,
                editable=False,
                help_text="When this item was originally created",
            ),
        ),
        migrations.AlterField(
            model_name="schedule",
            name="modified_on",
            field=models.DateTimeField(
                blank=True,
                default=django.utils.timezone.now,
                editable=False,
                help_text="When this item was last modified",
            ),
        ),
    ]
