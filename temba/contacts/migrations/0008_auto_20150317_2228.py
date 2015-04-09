# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0007_contactgroup_count'),
    ]

    operations = [
        migrations.RenameField(
            model_name='contact',
            old_name='is_archived',
            new_name='is_blocked',
        ),
    ]
