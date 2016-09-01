# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0041_indexes_update'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='exportcontactstask',
            name='host',
        ),
    ]
