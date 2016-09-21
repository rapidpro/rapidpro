# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.utils import chunk_list
from django.utils import timezone
from django.db.models import Count


def exit_active_flowruns(Contact, log=False):
    from temba.flows.models import FlowRun

    exit_runs = []

    # find all contacts that have more than one active run
    active_contact_ids = Contact.objects.filter(runs__is_active=True).order_by('id')\
        .annotate(run_count=Count('id')).filter(run_count__gt=1).values_list('id', flat=True)

    if log:
        print "%d contacts to evaluate runs for" % len(active_contact_ids)

    for idx, contact_id in enumerate(active_contact_ids):
        active_runs = FlowRun.objects.filter(contact_id=contact_id, is_active=True).order_by('-modified_on')

        # more than one? we may need to expire some
        if len(active_runs) > 1:
            last = active_runs[0]
            contact_exit_runs = [r.id for r in active_runs[1:]]
            ancestor = last.parent
            while ancestor:
                exit_runs.remove(ancestor.id)
                ancestor = ancestor.parent

            exit_runs += contact_exit_runs

        if (idx % 100) == 0:
            if log:
                print "  - %d / %d contacts evaluated. %d runs to exit" % (idx, len(active_contact_ids), len(exit_runs))

    # ok, now exit those runs
    exited = 0
    for batch in chunk_list(exit_runs, 1000):
        runs = FlowRun.objects.filter(id__in=batch)
        FlowRun.bulk_exit(runs, FlowRun.EXIT_TYPE_INTERRUPTED, timezone.now())

        exited += len(batch)
        if log:
            print " * %d / %d runs exited." % (exited, len(exit_runs))


def migration_exit_active_flowruns(apps, schema):
    exit_active_flowruns(apps.get_model('contacts', 'Contact'))


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0060_flowrun_timeout_on'),
        ('contacts', '0041_indexes_update'),
    ]

    operations = [
        migrations.RunPython(migration_exit_active_flowruns)
    ]
