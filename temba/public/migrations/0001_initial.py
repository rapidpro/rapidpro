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
            name='Lead',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('email', models.EmailField(max_length=75, error_messages={'unique': '{% trans "This email has already been registered." %}'})),
                ('created_by', models.ForeignKey(related_name=b'public_lead_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name=b'public_lead_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='Video',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('name', models.CharField(help_text='The name of the video', max_length=255, verbose_name='Name')),
                ('summary', models.TextField(help_text='A short blurb about the video', verbose_name='Summary')),
                ('description', models.TextField(help_text='The full description for the video', verbose_name='Description')),
                ('vimeo_id', models.CharField(help_text='The id vimeo uses for this video', max_length=255, verbose_name='Vimeo ID')),
                ('order', models.IntegerField(default=0)),
                ('created_by', models.ForeignKey(related_name=b'public_video_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name=b'public_video_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
    ]
