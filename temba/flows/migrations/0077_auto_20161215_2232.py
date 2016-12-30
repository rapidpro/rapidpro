# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.db.models import Count
from django_redis import get_redis_connection
from django.db import connection
from temba.utils import chunk_list
from temba.sql import InstallSQL
import time

HIGHPOINT_KEY = 'flowpathcount_backfill_highpoint'
CHUNK_SIZE = 200000
MAX_INT = 2147483647
LAST_SQUASH_KEY = 'last_flowpathcount_squash'

def dictfetchall(cursor):
    "Return all rows from a cursor as a dict"
    columns = [col[0] for col in cursor.description]
    return [
        dict(zip(columns, row))
        for row in cursor.fetchall()
    ]

def squash_counts(FlowPathCount):
    # get the id of the last count we squashed
    r = get_redis_connection()
    last_squash = r.get(LAST_SQUASH_KEY)
    if not last_squash:
        last_squash = 0

    # get the unique ids for all new ones
    start = time.time()
    squash_count = 0

    for count in FlowPathCount.objects.filter(id__gt=last_squash).order_by('flow_id', 'from_uuid', 'to_uuid', 'period') \
            .distinct('flow_id', 'from_uuid', 'to_uuid', 'period'):
        # perform our atomic squash in SQL by calling our squash method
        with connection.cursor() as c:
            c.execute("SELECT temba_squash_flowpathcount(%s, uuid(%s), uuid(%s), %s);",
                      (count.flow_id, count.from_uuid, count.to_uuid, count.period))

        squash_count += 1

    # insert our new top squashed id
    max_id = FlowPathCount.objects.all().order_by('-id').first()
    if max_id:
        r.set(LAST_SQUASH_KEY, max_id.id)

    print "Squashed flowpathcounts for %d combinations in %0.3fs" % (squash_count, time.time() - start)

def do_populate(Contact, FlowRun, FlowStep, FlowPathCount):

    r = get_redis_connection()

    highpoint = r.get(HIGHPOINT_KEY)
    if highpoint is None:
        highpoint = 0
    else:
        highpoint = int(highpoint)

    last_add = None

    print '\nStarting at %d' % highpoint

    while last_add is None or last_id < MAX_INT:

        start = time.time()

        test_contacts = Contact.objects.filter(is_test=True).values_list('id', flat=True)

        counts = []
        last_id = highpoint + CHUNK_SIZE

        # jump to the end if we didnt record any last time
        if last_add == 0:
            last_id = MAX_INT

        query = "SELECT max(fs.id) as max_id, fr.flow_id as \"flow_id\", step_uuid, next_uuid, rule_uuid, count(*), date_trunc('hour', left_on) as \"period\" "
        query += "FROM flows_flowstep fs, flows_flowrun fr, contacts_contact c "
        query += "WHERE fs.run_id = fr.id AND fs.contact_id = c.id AND c.is_test = False AND fs.left_on is not null "
        query += "AND fs.id > %s AND fs.id <= %s GROUP BY fr.flow_id, fs.step_uuid, fs.next_uuid, fs.rule_uuid, period;"

        with connection.cursor() as cursor:
            cursor.execute(query, [highpoint, last_id])
            results = dictfetchall(cursor)

            max_id = 0
            for result in results:
                from_uuid = result.get('rule_uuid')
                if not from_uuid:
                    from_uuid = result.get('step_uuid')

                if max_id < result.get('max_id'):
                    max_id = result.get('max_id')

                counts.append(FlowPathCount(flow_id=result.get('flow_id'),
                                            from_uuid=from_uuid,
                                            to_uuid=result.get('next_uuid'),
                                            period=result.get('period'),
                                            count=result.get('count')))

            FlowPathCount.objects.bulk_create(counts)
            last_add = len(counts)

            seconds = time.time() - start
            total = len(counts)
            print 'Added %d counts (Max: %d) in %0.3fs (%0.0fs/s)' % (total, max_id, seconds, float(total) / float(seconds))

            if max_id > 0:
                r.set(HIGHPOINT_KEY, max_id)

            counts = []
            squash_counts(FlowPathCount)
            highpoint += CHUNK_SIZE


def apply_manual():
    from temba.flows.models import FlowRun, FlowStep, FlowPathCount
    from temba.contacts.models import Contact

    do_populate(Contact, FlowRun, FlowStep, FlowPathCount)

def apply_as_migration(apps, schema_editor):
    FlowRun = apps.get_model('flows', 'FlowRun')
    FlowStep = apps.get_model('flows', 'FlowStep')
    FlowPathCount = apps.get_model('flows', 'FlowPathCount')
    Contact = apps.get_model('contacts', 'Contact')

    do_populate(Contact, FlowRun, FlowStep, FlowPathCount)


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0076_auto_20161215_2209'),
    ]

    operations = [
        migrations.RunPython(apply_as_migration)
    ]
