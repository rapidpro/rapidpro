# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0026_add_org_non_active_index'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contacturn',
            name='urn',
            field=models.CharField(help_text='The Universal Resource Name as a string. ex: tel:+250788383383', max_length=255, choices=[('tel', 'Phone number'), ('twitter', 'Twitter handle'), ('ext', 'External identifier')]),
        ),
    ]
