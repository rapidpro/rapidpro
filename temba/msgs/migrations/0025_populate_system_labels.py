# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from django.db.models import Count, F
from django.utils import timezone


def populate_system_labels(apps, schema_editor):
    Org = apps.get_model('orgs', 'Org')
    SystemLabel = apps.get_model('msgs', 'SystemLabel')
    Msg = apps.get_model('msgs', 'Msg')
    Broadcast = apps.get_model('msgs', 'Broadcast')
    Call = apps.get_model('msgs', 'Call')
    start_time = timezone.now()

    # these should be consistent with those returned from SystemLabel.get_queryset
    SYSLABEL_QUERYSETS = {
        'I': Msg.objects.filter(direction='I', visibility='V', msg_type='I').exclude(contact__is_test=True),
        'W': Msg.objects.filter(direction='I', visibility='V', msg_type='F').exclude(contact__is_test=True),
        'A': Msg.objects.filter(direction='I', visibility='A').exclude(contact__is_test=True),
        'O': Msg.objects.filter(direction='O', visibility='V', status__in=('P', 'Q')).exclude(contact__is_test=True),
        'S': Msg.objects.filter(direction='O', visibility='V', status__in=('W', 'S', 'D')).exclude(contact__is_test=True),
        'X': Msg.objects.filter(direction='O', visibility='V', status='F').exclude(contact__is_test=True),
        'E': Broadcast.objects.all().exclude(schedule=None).exclude(contacts__is_test=True),
        'C': Call.objects.filter(is_active=True).exclude(contact__is_test=True)
    }

    for label_type, queryset in SYSLABEL_QUERYSETS.iteritems():
        # items created after this time will have been already included via triggers
        queryset = queryset.filter(created_on__lt=start_time)

        # grab aggregate counts for all orgs - faster than doing org by org
        counts_by_org_id = queryset.values('org').annotate(total=Count('org')).order_by('org')
        counts_by_org_id = {pair['org']: pair['total'] for pair in counts_by_org_id}
        total = sum(counts_by_org_id.values())

        if counts_by_org_id:
            print("Fetched org counts for system label type %s (total = %d)" % (label_type, total))

            for org in Org.objects.only('pk', 'name'):
                item_count = counts_by_org_id.get(org.pk, 0)

                if item_count:
                    # print(" > incrementing org '%s' count with %d" % (org.name, item_count))

                    # increment label count that might already have a value from triggers
                    SystemLabel.objects.filter(org=org, label_type=label_type).update(count=F('count') + item_count)


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0024_system_label_triggers'),
    ]

    operations = [
        migrations.RunPython(populate_system_labels)
    ]
