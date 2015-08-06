# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.db.models import Count

# language=SQL
TRIGGER_SQL = """
---------------------------------------------------------------------------------
-- Increment or decrement a system label
---------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  temba_increment_system_label(_org_id INT, _label_type CHAR(1), _add BOOLEAN)
RETURNS VOID AS $$
BEGIN
  IF _add THEN
    INSERT INTO msgs_systemlabel("org_id", "label_type", "count") VALUES(_org_id, _label_type, 1);
  ELSE
    INSERT INTO msgs_systemlabel("org_id", "label_type", "count") VALUES(_org_id, _label_type, -1);
  END IF;

  PERFORM temba_maybe_squash_systemlabel(_org_id, _label_type);
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------------------
-- Every 100 inserts or so this will squash the label by gathering the counts
----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_maybe_squash_systemlabel(_org_id INTEGER, _label_type CHAR(1))
RETURNS VOID AS $$
BEGIN
  IF RANDOM() < .001 THEN
    WITH deleted as (DELETE FROM msgs_systemlabel
      WHERE "org_id" = _org_id AND "label_type" = _label_type
      RETURNING "count")
      INSERT INTO msgs_systemlabel("org_id", "label_type", "count")
      VALUES (_org_id, _label_type, GREATEST(0, (SELECT SUM("count") FROM deleted)));
  END IF;
END;
$$ LANGUAGE plpgsql;
"""

def populate_system_labels(apps, schema_editor):
    Org = apps.get_model('orgs', 'Org')
    SystemLabel = apps.get_model('msgs', 'SystemLabel')
    Msg = apps.get_model('msgs', 'Msg')
    Broadcast = apps.get_model('msgs', 'Broadcast')
    Call = apps.get_model('msgs', 'Call')

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
        # grab aggregate counts for all orgs - faster than doing org by org
        counts_by_org_id = queryset.values('org').annotate(total=Count('org')).order_by('org')
        counts_by_org_id = {pair['org']: pair['total'] for pair in counts_by_org_id}
        total = sum(counts_by_org_id.values())

        if counts_by_org_id:
            print("Fetched org counts for system label type %s (total = %d)" % (label_type, total))

            for org in Org.objects.only('pk', 'name'):
                item_count = counts_by_org_id.get(org.pk, 0)

                if item_count:
                    print(" > setting org '%s' count with %d" % (org.name, item_count))

                    # increment label count that might already have a value from triggers
                    SystemLabel.objects.filter(org=org, label_type=label_type).update(count=item_count)

class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0028_populate_system_labels'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemlabel',
            name='count',
            field=models.IntegerField(default=0, help_text='Number of items with this system label'),
            preserve_default=True,
        ),
        migrations.AlterUniqueTogether(
            name='systemlabel',
            unique_together=set([]),
        ),
        migrations.AlterIndexTogether(
            name='systemlabel',
            index_together=set([('org', 'label_type')]),
        ),
        migrations.RunPython(populate_system_labels),
        migrations.RunSQL(TRIGGER_SQL),
    ]
