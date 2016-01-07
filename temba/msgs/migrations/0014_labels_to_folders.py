# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from collections import defaultdict
from django.db import models, migrations


def migrate_label_hierarchies(apps, schema_editor):
    Label = apps.get_model('msgs', 'Label')
    folder_count, rename_count = 0, 0

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
                    labels[l].name = '%s (%d)' % (name, l)
                    labels[l].save(update_fields=('name',))
                    rename_count += 1

        # get child labels by their parents
        hierarchy = defaultdict(list)
        for label in org_labels:
            if label.parent:
                hierarchy[label.parent].append(label)

        for parent, children in hierarchy.iteritems():
            # create folder to replace parent
            folder = Label.objects.create(org=org, name=parent.name, label_type='F',
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


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0013_label_folder_and_type'),
    ]

    operations = [
        migrations.RunPython(
            migrate_label_hierarchies
        ),
    ]
