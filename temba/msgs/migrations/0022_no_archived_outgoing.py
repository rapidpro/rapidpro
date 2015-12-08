# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


def fix_archived_outgoing(apps, schema_editor):
    Msg = apps.get_model('msgs', 'Msg')
    wonky = Msg.objects.filter(direction='O', visibility='A')
    updated = wonky.update(visibility='V')
    if updated:
        print "Fixed %d outgoing messages that were archived" % updated


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0021_exportmessagestask_is_finished'),
    ]

    operations = [
        migrations.RunPython(fix_archived_outgoing)
    ]
