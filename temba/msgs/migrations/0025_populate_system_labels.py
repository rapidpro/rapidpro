# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


SYS_LABEL_FILTERS = {
    'I': dict(direction='I', visibility='V', msg_type='I'),
    'W': dict(direction='I', visibility='V', msg_type='F'),
    'A': dict(direction='I', visibility='A'),
    'O': dict(direction='O', status='Q'),
    'S': dict(direction='O', status='S'),
    'X': dict(direction='O', status='F')
}

INSERT_BATCH_SIZE = 10000


def populate_system_labels(apps, schema_editor):
    Org = apps.get_model('orgs', 'Org')
    SystemLabel = apps.get_model('msgs', 'SystemLabel')
    Msg = apps.get_model('msgs', 'Msg')

    for org in Org.objects.all():
        print "Populating system labels for org %s" % org.name

        for label_type, msg_filter in SYS_LABEL_FILTERS.iteritems():
            print " > populating %s..." % label_type

            label = SystemLabel.objects.get(org=org, label_type=label_type)
            msgs = list(Msg.objects.filter(org=org).filter(**msg_filter).only('pk'))

            print " > fetched %d" % len(msgs)

            # insert into associative table in batches so we don't lock the table too long
            for i in xrange(0, len(msgs), INSERT_BATCH_SIZE):
                chunk = msgs[i:i + INSERT_BATCH_SIZE]
                label.msgs.add(*chunk)

            label.count = len(msgs)
            label.save(update_fields=('count',))


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0024_system_label_triggers'),
    ]

    operations = [
        migrations.RunPython(populate_system_labels)
    ]
