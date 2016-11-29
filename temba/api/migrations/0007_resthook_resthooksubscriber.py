# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0018_fix_org_groups'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('api', '0006_auto_20160617_1610'),
    ]

    operations = [
        migrations.CreateModel(
            name='Resthook',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text='Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text='When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text='When this item was last modified', auto_now=True)),
                ('slug', models.SlugField(help_text='A simple label for this event')),
                ('created_by', models.ForeignKey(related_name='api_resthook_creations', to=settings.AUTH_USER_MODEL, help_text='The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name='api_resthook_modifications', to=settings.AUTH_USER_MODEL, help_text='The user which last modified this item')),
                ('org', models.ForeignKey(help_text='The organization this resthook belongs to', related_name='resthooks', to='orgs.Org')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ResthookSubscriber',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text='Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text='When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text='When this item was last modified', auto_now=True)),
                ('target_url', models.URLField(help_text='The URL that we will call when our ruleset is reached')),
                ('created_by', models.ForeignKey(related_name='api_resthooksubscriber_creations', to=settings.AUTH_USER_MODEL, help_text='The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name='api_resthooksubscriber_modifications', to=settings.AUTH_USER_MODEL, help_text='The user which last modified this item')),
                ('resthook', models.ForeignKey(related_name='subscribers', to='api.Resthook', help_text='The resthook being subscribed to')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
