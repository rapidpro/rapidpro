# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


def strip_boundary_alias_name(apps, schema_editor):
    BoundaryAlias = apps.get_model('locations', 'BoundaryAlias')

    for alias in BoundaryAlias.objects.all():
        name = alias.name
        alias.name = name.strip()
        alias.save()


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0002_auto_20141126_2054'),
    ]

    operations = [
        migrations.RunPython(strip_boundary_alias_name)
    ]
