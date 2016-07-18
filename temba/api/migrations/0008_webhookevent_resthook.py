# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0007_resthook_resthooksubscriber'),
    ]

    operations = [
        migrations.AddField(
            model_name='webhookevent',
            name='resthook',
            field=models.ForeignKey(to='api.Resthook', help_text='The associated resthook to this event. (optional)', null=True),
        ),
    ]
