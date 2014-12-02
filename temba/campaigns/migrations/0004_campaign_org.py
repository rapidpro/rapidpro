# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0003_auto_20141126_2054'),
        ('orgs', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='org',
            field=models.ForeignKey(help_text=b'The organization this campaign exists for', to='orgs.Org'),
            preserve_default=True,
        ),
    ]
