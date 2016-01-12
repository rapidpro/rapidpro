# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
import mptt.fields


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0003_strip_boundary_alias_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='adminboundary',
            name='lft',
            field=models.PositiveIntegerField(
                default=0, editable=False, db_index=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='adminboundary',
            name='rght',
            field=models.PositiveIntegerField(
                default=1, editable=False, db_index=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='adminboundary',
            name='tree_id',
            field=models.PositiveIntegerField(
                default=2, editable=False, db_index=True),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='adminboundary',
            name='parent',
            field=mptt.fields.TreeForeignKey(
                related_name='children', blank=True, to='locations.AdminBoundary', null=True),
            preserve_default=True,
        ),
    ]
