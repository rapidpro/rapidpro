# Generated by Django 5.1.4 on 2024-12-11 15:34

from django.db import migrations

SQL = """
----------------------------------------------------------------------
-- Handles INSERT statements on msg table
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_msg_on_insert() RETURNS TRIGGER AS $$
BEGIN
    -- add broadcast counts for all new broadcast values
    INSERT INTO msgs_broadcastmsgcount("broadcast_id", "count", "is_squashed")
    SELECT broadcast_id, count(*), FALSE FROM newtab WHERE broadcast_id IS NOT NULL GROUP BY broadcast_id;

    -- add positive item counts for all rows which belong to a folder
    INSERT INTO orgs_itemcount("org_id", "scope", "count", "is_squashed")
    SELECT org_id, temba_msg_countscope(newtab), count(*), FALSE FROM newtab
    WHERE temba_msg_countscope(newtab) IS NOT NULL
    GROUP BY 1, 2;

    -- add channel counts for all messages with a channel
    INSERT INTO channels_channelcount("channel_id", "count_type", "day", "count", "is_squashed")
    SELECT channel_id, temba_msg_determine_channel_count_code(newtab), created_on::date, count(*), FALSE FROM newtab
    WHERE channel_id IS NOT NULL GROUP BY channel_id, temba_msg_determine_channel_count_code(newtab), created_on::date;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles DELETE statements on msg table
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_msg_on_delete() RETURNS TRIGGER AS $$
BEGIN
    -- add negative item counts for all rows that belonged to a folder
    INSERT INTO orgs_itemcount("org_id", "scope", "count", "is_squashed")
    SELECT org_id, temba_msg_countscope(oldtab), -count(*), FALSE FROM oldtab
    WHERE temba_msg_countscope(oldtab) IS NOT NULL
    GROUP BY 1, 2;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles UPDATE statements on msg table
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_msg_on_update() RETURNS TRIGGER AS $$
BEGIN
    -- add negative item counts for all rows that belonged to a folder they no longer belong to
    INSERT INTO orgs_itemcount("org_id", "scope", "count", "is_squashed")
    SELECT o.org_id, temba_msg_countscope(o), -count(*), FALSE FROM oldtab o
    INNER JOIN newtab n ON n.id = o.id
    WHERE temba_msg_countscope(o) IS DISTINCT FROM temba_msg_countscope(n) AND temba_msg_countscope(o) IS NOT NULL
    GROUP BY 1, 2;

    -- add positive item counts for all rows that now belong to a folder they didn't belong to
    INSERT INTO orgs_itemcount("org_id", "scope", "count", "is_squashed")
    SELECT n.org_id, temba_msg_countscope(n), count(*), FALSE FROM newtab n
    INNER JOIN oldtab o ON o.id = n.id
    WHERE temba_msg_countscope(o) IS DISTINCT FROM temba_msg_countscope(n) AND temba_msg_countscope(n) IS NOT NULL
    GROUP BY 1, 2;

    -- add negative old-state label counts for all messages being archived/restored
    INSERT INTO msgs_labelcount("label_id", "is_archived", "count", "is_squashed")
    SELECT ml.label_id, o.visibility != 'V', -count(*), FALSE FROM oldtab o
    INNER JOIN newtab n ON n.id = o.id
    INNER JOIN msgs_msg_labels ml ON ml.msg_id = o.id
    WHERE (o.visibility = 'V' AND n.visibility != 'V') or (o.visibility != 'V' AND n.visibility = 'V')
    GROUP BY 1, 2;

    -- add new-state label counts for all messages being archived/restored
    INSERT INTO msgs_labelcount("label_id", "is_archived", "count", "is_squashed")
    SELECT ml.label_id, n.visibility != 'V', count(*), FALSE FROM newtab n
    INNER JOIN oldtab o ON o.id = n.id
    INNER JOIN msgs_msg_labels ml ON ml.msg_id = n.id
    WHERE (o.visibility = 'V' AND n.visibility != 'V') or (o.visibility != 'V' AND n.visibility = 'V')
    GROUP BY 1, 2;

    -- add new flow activity counts for incoming messages now marked as handled by a flow
    INSERT INTO flows_flowactivitycount("flow_id", "scope", "count", "is_squashed")
    SELECT s.flow_id, unnest(ARRAY[
            format('msgsin:hour:%s', extract(hour FROM NOW())),
            format('msgsin:dow:%s', extract(isodow FROM NOW())),
            format('msgsin:date:%s', NOW()::date)
        ]), s.msgs, FALSE
    FROM (
        SELECT n.flow_id, count(*) AS msgs FROM newtab n INNER JOIN oldtab o ON o.id = n.id
        WHERE n.direction = 'I' AND o.flow_id IS NULL AND n.flow_id IS NOT NULL
        GROUP BY 1
    ) s;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles INSERT statements on broadcast table
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_broadcast_on_insert() RETURNS TRIGGER AS $$
BEGIN
    -- add positive item counts for all rows which belong to a folder
    INSERT INTO orgs_itemcount("org_id", "scope", "count", "is_squashed")
    SELECT org_id, temba_broadcast_countscope(newtab), count(*), FALSE FROM newtab
    WHERE temba_broadcast_countscope(newtab) IS NOT NULL
    GROUP BY 1, 2;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles DELETE statements on broadcast table
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_broadcast_on_delete() RETURNS TRIGGER AS $$
BEGIN
    -- add negative item counts for all rows that belonged to a folder
    INSERT INTO orgs_itemcount("org_id", "scope", "count", "is_squashed")
    SELECT org_id, temba_broadcast_countscope(oldtab), -count(*), FALSE FROM oldtab
    WHERE temba_broadcast_countscope(oldtab) IS NOT NULL
    GROUP BY 1, 2;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles UPDATE statements on broadcast table
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_broadcast_on_update() RETURNS TRIGGER AS $$
BEGIN
    -- add negative counts for all old non-null item count scopes that don't match the new ones
    INSERT INTO orgs_itemcount("org_id", "scope", "count", "is_squashed")
    SELECT o.org_id, temba_broadcast_countscope(o), -count(*), FALSE FROM oldtab o
    INNER JOIN newtab n ON n.id = o.id
    WHERE temba_broadcast_countscope(o) IS DISTINCT FROM temba_broadcast_countscope(n) AND temba_broadcast_countscope(o) IS NOT NULL
    GROUP BY 1, 2;

    -- add positive counts for all new non-null item counts that don't match the old ones
    INSERT INTO orgs_itemcount("org_id", "scope", "count", "is_squashed")
    SELECT n.org_id, temba_broadcast_countscope(n), count(*), FALSE FROM newtab n
    INNER JOIN oldtab o ON o.id = n.id
    WHERE temba_broadcast_countscope(o) IS DISTINCT FROM temba_broadcast_countscope(n) AND temba_broadcast_countscope(n) IS NOT NULL
    GROUP BY 1, 2;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles INSERT statements on ivr_call table
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_ivrcall_on_insert() RETURNS TRIGGER AS $$
BEGIN
    -- add positive item counts for all rows being inserted
    INSERT INTO orgs_itemcount("org_id", "scope", "count", "is_squashed")
    SELECT org_id, 'msgs:folder:C', count(*), FALSE FROM newtab GROUP BY 1;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Handles DELETE statements on ivr_call table
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_ivrcall_on_delete() RETURNS TRIGGER AS $$
BEGIN
    -- add negative item counts for all rows being deleted
    INSERT INTO orgs_itemcount("org_id", "scope", "count", "is_squashed")
    SELECT org_id, 'msgs:folder:C', -count(*), FALSE FROM oldtab GROUP BY 1;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP FUNCTION temba_broadcast_determine_system_label(msgs_broadcast);
DROP FUNCTION temba_msg_determine_system_label(msgs_msg);
"""


class Migration(migrations.Migration):

    dependencies = [("msgs", "0279_backfill_new_counts")]

    operations = [migrations.RunSQL(SQL)]
