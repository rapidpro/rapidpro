# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations, connection
from django.db.models import Count
from temba.sql import InstallSQL

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0048_auto_20160126_2305'),
    ]

    def clear_flowrun_counts(apps, schema_editor):
        """
        Clears all flowrun counts
        """
        FlowRunCount = apps.get_model('flows', 'FlowRunCount')
        FlowRunCount.objects.all().delete()

    def backfill_flowrun_counts(apps, schema_editor):
        """
        Backfills our counts for all flows
        """
        Flow = apps.get_model('flows', 'Flow')
        FlowRun = apps.get_model('flows', 'FlowRun')
        FlowRunCount = apps.get_model('flows', 'FlowRunCount')
        Contact = apps.get_model('contacts', 'Contact')

        # for each flow that has at least one run
        for flow in Flow.objects.exclude(runs=None):
            # get test contacts on this org
            test_contacts = Contact.objects.filter(org=flow.org, is_test=True).values('id')

            # calculate our count for each exit type
            counts = FlowRun.objects.filter(flow=flow).exclude(contact__in=test_contacts)\
                                    .values('exit_type').annotate(Count('exit_type'))

            # remove old ones
            FlowRunCount.objects.filter(flow=flow).delete()

            # insert updated counts for each
            for count in counts:
                if count['exit_type__count'] > 0:
                    FlowRunCount.objects.create(flow=flow, exit_type=count['exit_type'], count=count['exit_type__count'])

            print "%s - %s" % (flow.name, counts)

    operations = [
        InstallSQL('0049_flows'),
        migrations.RunPython(
            backfill_flowrun_counts, clear_flowrun_counts
        )
    ]

