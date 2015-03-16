# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.utils.timezone import utc
import datetime
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('msgs', '0008_remove_label_label_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='label',
            name='created_by',
            field=models.ForeignKey(related_name='msgs_label_creations', default=1, to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='label',
            name='created_on',
            field=models.DateTimeField(default=datetime.datetime(2015, 3, 12, 11, 13, 17, 5971, tzinfo=utc), help_text=b'When this item was originally created', auto_now_add=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='label',
            name='is_active',
            field=models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='label',
            name='modified_by',
            field=models.ForeignKey(related_name='msgs_label_modifications', default=1, to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='label',
            name='modified_on',
            field=models.DateTimeField(default=datetime.datetime(2015, 3, 12, 11, 13, 32, 878068, tzinfo=utc), help_text=b'When this item was last modified', auto_now=True),
            preserve_default=False,
        ),
    ]
