# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('api', '0002_auto_20141126_2054'),
        ('orgs', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='webhookevent',
            name='org',
            field=models.ForeignKey(help_text='The organization that this event was triggered for', to='orgs.Org'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='apitoken',
            name='org',
            field=models.ForeignKey(related_name='api_tokens', to='orgs.Org'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='apitoken',
            name='user',
            field=models.ForeignKey(related_name='api_tokens', to=settings.AUTH_USER_MODEL),
            preserve_default=True,
        ),
        migrations.AlterUniqueTogether(
            name='apitoken',
            unique_together=set([('user', 'org')]),
        ),
    ]
