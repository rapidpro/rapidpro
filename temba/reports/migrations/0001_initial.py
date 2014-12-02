# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('orgs', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Report',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('title', models.CharField(help_text='The name title or this report', max_length=64, verbose_name='Title')),
                ('description', models.TextField(help_text='The full description for the report', verbose_name='Description')),
                ('config', models.TextField(help_text='The JSON encoded configurations for this report', null=True, verbose_name='Configuration')),
                ('is_published', models.BooleanField(default=False, help_text='Whether this report is currently published')),
                ('created_by', models.ForeignKey(related_name=b'reports_report_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name=b'reports_report_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
                ('org', models.ForeignKey(to='orgs.Org')),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.AlterUniqueTogether(
            name='report',
            unique_together=set([('org', 'title')]),
        ),
    ]
