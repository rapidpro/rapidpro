import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [("airtime", "0005_reset_3"), ("orgs", "0029_reset_1")]

    operations = [
        migrations.AddField(
            model_name="airtimetransfer",
            name="org",
            field=models.ForeignKey(
                help_text="The organization that this airtime was triggered for",
                on_delete=django.db.models.deletion.CASCADE,
                to="orgs.Org",
            ),
        )
    ]
