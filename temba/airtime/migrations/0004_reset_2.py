import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [("airtime", "0003_reset_1"), ("channels", "0050_reset_1")]

    operations = [
        migrations.AddField(
            model_name="airtimetransfer",
            name="channel",
            field=models.ForeignKey(
                blank=True,
                help_text="The channel that this airtime is relating to",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="channels.Channel",
            ),
        )
    ]
