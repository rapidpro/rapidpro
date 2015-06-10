# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from collections import defaultdict
from django.db import models, migrations
import temba.utils.models
from django.conf import settings


def migrate_label_hierarchies(apps, schema_editor):
    Label = apps.get_model('msgs', 'Label')
    LabelFolder = apps.get_model('msgs', 'LabelFolder')
    folder_count = 0

    # fetch all labels and organize by org
    labels_by_org = defaultdict(list)
    for label in Label.objects.all().select_related('org', 'parent'):
        labels_by_org[label.org].append(label)

    for org, org_labels in labels_by_org.iteritems():
        # check for duplicate names
        labels_by_name = defaultdict(list)
        for label in org_labels:
            labels_by_name[label.name].append(label)

        # and rename as required
        for name, labels in labels_by_name.iteritems():
            if len(labels) > 1:
                for l in range(1, len(labels)):
                    labels[l].name = '%s %d' % (name, l)
                    labels[l].save(update_fields=('name',))

        # get child labels by their parents
        hierarchy = defaultdict(list)
        for label in org_labels:
            if label.parent:
                hierarchy[label.parent].append(label)

        for parent, children in hierarchy.iteritems():
            # create folder to replace parent
            folder = LabelFolder.objects.create(org=org, name=parent.name,
                                                created_by=parent.created_by, created_on=parent.created_on,
                                                modified_by=parent.modified_by, modified_on=parent.modified_on)
            folder_count += 1

            # put child labels in folder instead of under parent
            Label.objects.filter(pk__in=[c.pk for c in children]).update(folder=folder, parent=None)

            # if parent has msgs itself then put it in the folder, otherwise can delete it
            if parent.msgs.exists():
                parent.name = '%s (old)' % parent.name
                parent.folder = folder
                parent.save(update_fields=('name', 'folder'))
            else:
                parent.delete()

    print "Converted %d labels to folders" % folder_count


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
        migrations.RunPython(
            migrate_label_hierarchies
        ),
        migrations.AlterUniqueTogether(
            name='label',
            unique_together=set([('org', 'name')]),
        ),
        migrations.RemoveField(
            model_name='label',
            name='parent',
        ),
    ]
