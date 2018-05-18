from django.db import migrations
import temba.utils.models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0016_webhookevent_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='webhookevent',
            name='data',
            field=temba.utils.models.JSONAsTextField(help_text='The JSON encoded data that will be POSTED to the web hook', default=dict),
        ),
    ]
