# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0002_auto_20141126_2054'),
    ]

    operations = [
        migrations.AddField(
            model_name='adminboundary',
            name='in_country',
            field=models.CharField(help_text=b"The OSM id of this admin level's country id", max_length=15, null=True),
            preserve_default=True,
        ),
    ]
