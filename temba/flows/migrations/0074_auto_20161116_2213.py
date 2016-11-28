# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0073_auto_20161111_1534'),
        ('channels', '0042_ivrcall')
    ]

    operations = [
        migrations.AlterField(
            model_name='flowrun',
            name='call',
            field=models.ForeignKey(related_name='runs', blank=True, to='channels.ChannelSession',
                                    help_text='The session that handled this flow run, only for voice flows', null=True),
        ),
        migrations.RenameField(
            model_name='flowrun',
            old_name='call',
            new_name='session',
        ),
    ]
