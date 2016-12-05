# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0072_auto_20160905_1537'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowrun',
            name='call',
            field=models.ForeignKey(related_name='runs', blank=True, to='channels.IVRCall', help_text='The call that handled this flow run, only for voice flows', null=True),
        ),
    ]
