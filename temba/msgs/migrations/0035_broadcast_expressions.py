# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from django.db.models import Q
from temba.utils.expressions import migrate_template


def migrate_broadcasts(apps, schema_editor):
    Broadcast = apps.get_model('msgs', 'Broadcast')

    num_migrated = 0
    num_unchanged = 0

    for broadcast in Broadcast.objects.filter(Q(status='I') | ~Q(schedule=None)):
        migrated = migrate_template(broadcast.text)
        if migrated != broadcast.text:
            broadcast.text = migrated
            broadcast.save(update_fields=('text',))
            print '"%s" -> "%s"' % (broadcast.text, migrated)
            num_migrated += 1
        else:
            num_unchanged += 1

    if num_migrated or num_unchanged:
        print "Migrated expressions in %d broadcasts (%d unchanged)" % (num_migrated, num_unchanged)


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0034_move_recording_domains'),
    ]

    operations = [
        migrations.RunPython(migrate_broadcasts)
    ]
