# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.utils.timezone import utc
import datetime
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contacts', '0030_auto_20160202_1931'),
    ]

    operations = [
        migrations.AddField(
            model_name='contactfield',
            name='created_by',
            field=models.ForeignKey(related_name='contacts_contactfield_creations', default=settings.ANONYMOUS_USER_ID, to=settings.AUTH_USER_MODEL, help_text='The user which originally created this item'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='contactfield',
            name='created_on',
            field=models.DateTimeField(default=datetime.datetime(2014, 1, 1, 0, 0, 0, 0, tzinfo=utc), help_text='When this item was originally created', auto_now_add=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='contactfield',
            name='modified_by',
            field=models.ForeignKey(related_name='contacts_contactfield_modifications', default=settings.ANONYMOUS_USER_ID, to=settings.AUTH_USER_MODEL, help_text='The user which last modified this item'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='contactfield',
            name='modified_on',
            field=models.DateTimeField(default=datetime.datetime(2014, 1, 1, 0, 0, 0, 0, tzinfo=utc), help_text='When this item was last modified', auto_now=True),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='contactfield',
            name='is_active',
            field=models.BooleanField(default=True, help_text='Whether this item is active, use this instead of deleting'),
        ),
    ]
