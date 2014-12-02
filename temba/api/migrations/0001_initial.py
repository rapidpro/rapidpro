# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='APIToken',
            fields=[
                ('key', models.CharField(max_length=40, serialize=False, primary_key=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='WebHookEvent',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('status', models.CharField(default='P', help_text='The state this event is currently in', max_length=1, choices=[('P', 'Pending'), ('C', 'Complete'), ('E', 'Errored'), ('F', 'Failed')])),
                ('event', models.CharField(help_text='The event type for this event', max_length=16, choices=[('mo_sms', 'Incoming SMS Message'), ('mt_sent', 'Outgoing SMS Sent'), ('mt_dlvd', 'Outgoing SMS Delivered to Recipient'), ('mt_call', 'Outgoing Call'), ('mt_miss', 'Missed Outgoing Call'), ('mo_call', 'Incoming Call'), ('mo_miss', 'Missed Incoming Call'), ('alarm', 'Channel Alarm'), ('flow', 'Flow Step Reached'), ('categorize', 'Flow Categorization')])),
                ('data', models.TextField(help_text='The JSON encoded data that will be POSTED to the web hook')),
                ('try_count', models.IntegerField(default=0, help_text='The number of times this event has been tried')),
                ('next_attempt', models.DateTimeField(help_text='When this event will be retried', null=True, blank=True)),
                ('action', models.CharField(default='POST', help_text='What type of HTTP event is it', max_length=8)),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='WebHookResult',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('url', models.TextField(help_text='The URL the event was delivered to', null=True, blank=True)),
                ('data', models.TextField(help_text='The data that was posted to the webhook', null=True, blank=True)),
                ('status_code', models.IntegerField(help_text='The HTTP status as returned by the web hook')),
                ('message', models.CharField(help_text='A message describing the result, error messages go here', max_length=255)),
                ('body', models.TextField(help_text='The body of the HTTP response as returned by the web hook', null=True, blank=True)),
                ('created_by', models.ForeignKey(related_name=b'api_webhookresult_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('event', models.ForeignKey(help_text='The event that this result is tied to', to='api.WebHookEvent')),
                ('modified_by', models.ForeignKey(related_name=b'api_webhookresult_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
    ]
