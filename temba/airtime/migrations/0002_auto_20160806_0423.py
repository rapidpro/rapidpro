# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('airtime', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='airtimetransfer',
            name='status',
            field=models.CharField(default=b'P', help_text=b'The state this event is currently in', max_length=1, choices=[(b'P', b'Pending'), (b'S', b'Success'), (b'F', b'Failed')]),
        ),
    ]
