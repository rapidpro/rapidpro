from django.db import migrations

import temba.utils.models


class Migration(migrations.Migration):

    dependencies = [("campaigns", "0018_auto_20170606_1326")]

    operations = [
        migrations.AlterField(
            model_name="campaignevent",
            name="message",
            field=temba.utils.models.TranslatableField(max_length=640, null=True),
        )
    ]
