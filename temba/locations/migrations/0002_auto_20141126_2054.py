# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0001_initial'),
        ('orgs', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='boundaryalias',
            name='org',
            field=models.ForeignKey(
                help_text=b'The org that owns this alias', to='orgs.Org'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='adminboundary',
            name='parent',
            field=models.ForeignKey(related_name=b'children', to='locations.AdminBoundary',
                                    help_text=b'The parent to this political boundary if any', null=True),
            preserve_default=True,
        ),
    ]
