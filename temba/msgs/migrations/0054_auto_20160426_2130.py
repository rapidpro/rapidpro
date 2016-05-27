# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0035_auto_20160414_0642'),
        ('msgs', '0053_auto_20160415_1337'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcast',
            name='recipients',
            field=models.ManyToManyField(help_text='The URNs which received this message', related_name='broadcasts', verbose_name='Recipients', to='contacts.ContactURN'),
        ),
        migrations.AlterField(
            model_name='broadcast',
            name='contacts',
            field=models.ManyToManyField(help_text='Individual contacts included in this message', related_name='addressed_broadcasts', verbose_name='Contacts', to='contacts.Contact'),
        ),
        migrations.AlterField(
            model_name='broadcast',
            name='groups',
            field=models.ManyToManyField(help_text='The groups to send the message to', related_name='addressed_broadcasts', verbose_name='Groups', to='contacts.ContactGroup'),
        ),
        migrations.AlterField(
            model_name='broadcast',
            name='recipient_count',
            field=models.IntegerField(help_text='Number of urns which received this broadcast', null=True, verbose_name='Number of recipients'),
        ),
        migrations.AlterField(
            model_name='broadcast',
            name='urns',
            field=models.ManyToManyField(help_text='Individual URNs included in this message', related_name='addressed_broadcasts', verbose_name='URNs', to='contacts.ContactURN'),
        ),
    ]
