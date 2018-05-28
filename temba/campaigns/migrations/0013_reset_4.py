import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("campaigns", "0012_reset_3"), ("orgs", "0029_reset_1")]

    operations = [
        migrations.AddField(
            model_name="campaign",
            name="org",
            field=models.ForeignKey(
                help_text="The organization this campaign exists for",
                on_delete=django.db.models.deletion.CASCADE,
                to="orgs.Org",
            ),
        )
    ]
