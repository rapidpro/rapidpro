from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("locations", "0011_add_path_index")]

    operations = [
        migrations.AlterField(
            model_name="adminboundary",
            name="path",
            field=models.CharField(help_text="The full path name for this location", max_length=768),
        )
    ]
