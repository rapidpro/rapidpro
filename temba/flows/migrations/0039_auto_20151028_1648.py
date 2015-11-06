# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('flows', '0038_auto_20151028_1640'),
    ]

    operations = [
        migrations.RenameModel(old_name='FlowVersion', new_name='FlowRevision'),
    ]
