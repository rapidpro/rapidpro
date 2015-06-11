# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
import temba.utils.models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('orgs', '0003_auto_20150313_1624'),
        ('msgs', '0011_remove_exportmessagestask_filename'),
    ]

    operations = [
        migrations.CreateModel(
            name='LabelFolder',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('uuid', models.CharField(default=temba.utils.models.generate_uuid, max_length=36, help_text='The unique identifier for this object', unique=True, verbose_name='Unique Identifier', db_index=True)),
                ('name', models.CharField(help_text='The name of this folder', max_length=64, verbose_name='Name')),
                ('created_by', models.ForeignKey(related_name='msgs_labelfolder_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name='msgs_labelfolder_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
                ('org', models.ForeignKey(to='orgs.Org')),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.AlterUniqueTogether(
            name='labelfolder',
            unique_together=set([('org', 'name')]),
        ),
        migrations.AddField(
            model_name='label',
            name='folder',
            field=models.ForeignKey(related_name='labels', verbose_name='Folder', to='msgs.LabelFolder', null=True),
            preserve_default=True,
        ),
    ]
