# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0072_auto_20160905_1537'),
        ('msgs', '0067_auto_20161005_0731'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcast',
            name='flow',
            field=models.ForeignKey(to='flows.Flow', help_text='The flow that created this broadcast', null=True),
        )
    ]
