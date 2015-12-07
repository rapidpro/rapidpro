# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0005_auto_20150416_0729'),
        ('msgs', '0023_released_contact_calls'),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemLabel',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('label_type', models.CharField(max_length=1, choices=[('I', 'Inbox'), ('W', 'Flows'), ('A', 'Archived'), ('O', 'Outbox'), ('S', 'Sent'), ('X', 'Failed'), ('E', 'Scheduled'), ('C', 'Calls')])),
                ('count', models.PositiveIntegerField(default=0, help_text='Number of items with this system label')),
                ('org', models.ForeignKey(related_name='system_labels', to='orgs.Org')),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.AlterUniqueTogether(
            name='systemlabel',
            unique_together=set([('org', 'label_type')]),
        ),
        migrations.AlterField(
            model_name='label',
            name='label_type',
            field=models.CharField(default='L', help_text='Label type', max_length=1, choices=[('F', 'Folder of labels'), ('L', 'Regular label')]),
            preserve_default=True,
        ),
    ]
