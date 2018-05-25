from django.db import migrations
import temba.utils.models


class Migration(migrations.Migration):

    dependencies = [("campaigns", "0017_auto_20170508_1540")]

    operations = [
        migrations.AlterField(
            model_name="campaignevent",
            name="message",
            field=temba.utils.models.TranslatableField(max_length=8000, null=True),
        )
    ]
