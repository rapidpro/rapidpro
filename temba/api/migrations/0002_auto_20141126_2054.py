# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('channels', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='webhookevent',
            name='channel',
            field=models.ForeignKey(blank=True, to='channels.Channel', help_text='The channel that this event is relating to', null=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='webhookevent',
            name='created_by',
            field=models.ForeignKey(related_name=b'api_webhookevent_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='webhookevent',
            name='modified_by',
            field=models.ForeignKey(related_name=b'api_webhookevent_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item'),
            preserve_default=True,
        ),
    ]
