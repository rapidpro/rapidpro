# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0057_update_triggers'),
        ('flows', '0053_auto_20160414_0642'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowstep',
            name='broadcasts',
            field=models.ManyToManyField(help_text='Any broadcasts that are associated with this step (only sent)', related_name='steps', to='msgs.Broadcast'),
        ),
    ]
