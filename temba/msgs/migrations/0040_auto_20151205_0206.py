# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from functools import wraps

from django.db import migrations, models
from django.db.models import Max

BATCH_SIZE = 1000


def non_atomic_migration(func):
    @wraps(func)
    def wrapper(apps, schema_editor):
        if schema_editor.connection.in_atomic_block:
            schema_editor.atomic.__exit__(None, None, None)
        return func(apps, schema_editor)
    return wrapper


@non_atomic_migration
def initialize_data(apps, schema_editor):

    Msg = apps.get_model("msgs", "Msg")
    max_pk = Msg.objects.aggregate(Max('pk'))['pk__max']
    if max_pk is not None:
        print "Populating msg purged field.."
        for offset in range(0, max_pk+1, BATCH_SIZE):
            print 'On %d of %d' % (offset, max_pk)
            (Msg.objects
             .filter(pk__gte=offset)
             .filter(pk__lt=offset+BATCH_SIZE)
             .filter(purged__isnull=True)
             .update(purged=False))

    Broadcast = apps.get_model("msgs", "Broadcast")
    max_pk = Broadcast.objects.aggregate(Max('pk'))['pk__max']
    if max_pk is not None:
        print "Populating broadcast purged field.."
        for offset in range(0, max_pk+1, BATCH_SIZE):
            print 'On %d of %d' % (offset, max_pk)
            (Broadcast.objects
             .filter(pk__gte=offset)
             .filter(pk__lt=offset+BATCH_SIZE)
             .filter(purged__isnull=True)
             .update(purged=False))


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0039_auto_20151204_2238'),
    ]

    operations = [
        migrations.RunPython(initialize_data, atomic=False),
    ]
