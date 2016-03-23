# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL

class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0033_auto_20151116_1433'),
    ]

    def populate_contactgroupcounts(apps, schema_editor):
        from temba.contacts.models import ContactGroup, ContactGroupCount
        for group_id in ContactGroup.all_groups.all().values_list('id', flat=True):
            group = ContactGroup.all_groups.get(id=group_id)
            count = ContactGroupCount.populate_for_group(group)
            print "%s %d" % (group.name, count.count)

    operations = [
        migrations.CreateModel(
            name='ContactGroupCount',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('count', models.IntegerField(default=0)),
                ('group', models.ForeignKey(related_name='counts', to='contacts.ContactGroup', db_index=True)),
            ],
        ),
        migrations.AlterField(
            model_name='contactfield',
            name='value_type',
            field=models.CharField(default='T', max_length=1, verbose_name='Field Type', choices=[('T', 'Text'), ('N', 'Numeric'), ('D', 'Date & Time'), ('S', 'State'), ('I', 'District'), ('W', 'Ward')]),
        ),
        migrations.AlterField(
            model_name='contacturn',
            name='urn',
            field=models.CharField(help_text='The Universal Resource Name as a string. ex: tel:+250788383383', max_length=255, choices=[('tel', 'Phone number'), ('twitter', 'Twitter handle'), ('telegram', 'Telegram identifier'), ('mailto', 'Email address'), ('ext', 'External identifier')]),
        ),
        InstallSQL('0034_contacts'),
        migrations.RunPython(populate_contactgroupcounts),
    ]
