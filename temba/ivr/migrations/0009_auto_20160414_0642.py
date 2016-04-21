# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ivr', '0008_ivrcall_parent'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ivrcall',
            name='parent',
            field=models.ForeignKey(related_name='child_calls', verbose_name='Parent Call', to='ivr.IVRCall', help_text='The call that triggered this one', null=True),
        ),
    ]
