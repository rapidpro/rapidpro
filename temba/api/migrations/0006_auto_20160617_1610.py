# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0005_apitoken_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='apitoken',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterUniqueTogether(
            name='apitoken',
            unique_together=set([]),
        ),
    ]
