# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os

from django.core.files.storage import default_storage
from django.db import migrations
from temba.assets.models import AssetType


def migrate_export_tasks(apps, schema_editor):
    task_model = apps.get_model('flows', 'ExportFlowResultsTask')
    store = AssetType.results_export.store

    copied_task_ids = []
    failed_task_ids = []

    for task in task_model.objects.exclude(filename=None):
        identifier = task.pk
        extension = os.path.splitext(task.filename)[1][1:]

        try:
            existing_file = default_storage.open(task.filename)
            new_path = store.derive_path(task.org, identifier, extension)
            default_storage.save(new_path, existing_file)
            copied_task_ids.append(task.pk)

            task.filename = None
            task.save()
        except Exception:
            print "Unable to copy %s" % task.filename
            failed_task_ids.append(task.pk)

    # clear filename for tasks that were successfully copied so we don't try to migrate them again
    task_model.objects.filter(pk__in=copied_task_ids).update(filename=None)

    if len(copied_task_ids) + len(failed_task_ids) > 0:
        print 'Copied %d export task files (%d could not be copied)' % (len(copied_task_ids), len(failed_task_ids))


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0015_auto_20150320_0417'),
    ]

    operations = [
        migrations.RunPython(migrate_export_tasks)
    ]
