# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0023_remove_org_multi_org'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='invitation',
            name='host',
        ),
    ]
