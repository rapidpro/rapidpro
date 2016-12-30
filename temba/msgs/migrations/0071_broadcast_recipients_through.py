# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0043_auto_20161111_1850'),
        ('msgs', '0070_broadcast_purged_nonull'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.CreateModel(
                name='BroadcastRecipient',
                fields=[
                    ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ],
                options={
                    'db_table': 'msgs_broadcast_recipients',
                },
            ),
            migrations.AlterField(
                model_name='broadcast',
                name='recipients',
                field=models.ManyToManyField(help_text='The contacts which received this message', related_name='broadcasts', verbose_name='Recipients', through='msgs.BroadcastRecipient', to='contacts.Contact'),
            ),
            migrations.AddField(
                model_name='broadcastrecipient',
                name='broadcast',
                field=models.ForeignKey(to='msgs.Broadcast'),
            ),
            migrations.AddField(
                model_name='broadcastrecipient',
                name='contact',
                field=models.ForeignKey(to='contacts.Contact'),
            ),
        ])
    ]
