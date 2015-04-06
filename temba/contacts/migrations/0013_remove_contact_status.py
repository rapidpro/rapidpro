# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0012_contactgroup_group_type')
    ]

    operations = [
        migrations.RemoveField(
            model_name='contact',
            name='status',
        ),
    ]
