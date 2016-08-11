# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ivr', '0007_auto_20150203_0743'),
    ]

    operations = [
        migrations.AddField(
            model_name='ivrcall',
            name='parent',
            field=models.ForeignKey(verbose_name='Parent Call', to='ivr.IVRCall', help_text='The call that triggered this one', null=True),
        ),
    ]
