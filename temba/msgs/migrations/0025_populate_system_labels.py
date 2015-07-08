# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


SYS_LABEL_FILTERS = {
    'I': dict(direction='I', visibility='V', msg_type='I'),
    'W': dict(direction='I', visibility='V', msg_type='F'),
    'A': dict(direction='I', visibility='A'),
    'O': dict(direction='O', status__in=('P', 'Q')),
    'S': dict(direction='O', status__in=('W', 'S', 'D')),
    'X': dict(direction='O', status='F')
}


def populate_system_labels(apps, schema_editor):
    Org = apps.get_model('orgs', 'Org')
    SystemLabel = apps.get_model('msgs', 'SystemLabel')
    Msg = apps.get_model('msgs', 'Msg')

    orgs = Org.objects.all()

    for index, org in enumerate(orgs):
        print "Populating system labels for org %s (%d of %d)" % (org.name, (index+1), len(orgs))

        for label_type, msg_filter in SYS_LABEL_FILTERS.iteritems():
            print " > populating %s..." % label_type

            label = SystemLabel.objects.get(org=org, label_type=label_type)
            msgs = list(Msg.objects.filter(org=org).filter(**msg_filter).only('pk'))

            print " > fetched %d" % len(msgs)

            # we won't maintain an associative relationship for Flows or Sent
            if label_type not in ('W', 'S'):
                label.msgs.add(*msgs)

            label.count = len(msgs)
            label.save(update_fields=('count',))


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0024_system_label_triggers'),
    ]

    operations = [
        migrations.RunPython(populate_system_labels)
    ]
