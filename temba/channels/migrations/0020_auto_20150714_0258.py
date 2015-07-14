# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.db.models import Count

class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0019_update_channellog_triggers'),
    ]

    # this used to be a fix for another migration, but that has been applied earlier
    def noop(apps, schema_editor):
        pass

    operations = [
        migrations.RunPython(
            noop,
        ),
    ]
