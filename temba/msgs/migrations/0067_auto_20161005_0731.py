# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0066_external_id_partial_index'),
    ]

    operations = [
        migrations.AlterField(
            model_name='msg',
            name='external_id',
            field=models.CharField(help_text='External id used for integrating with callbacks from other APIs', max_length=255, null=True, verbose_name='External ID', blank=True),
        ),
    ]
