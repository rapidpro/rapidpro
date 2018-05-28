from django.db import migrations, models

SQL = """
-- indexes for fast fetching of unsquashed rows
CREATE INDEX orgs_debit_unsquashed_purged
ON orgs_debit(topup_id) WHERE NOT is_squashed AND debit_type = 'P';

CREATE INDEX orgs_topupcredits_unsquashed
ON orgs_topupcredits(topup_id) WHERE NOT is_squashed;

-- this is performed in Python-land now
DROP FUNCTION temba_squash_topupcredits(INTEGER);

---------------------------------------------------------------------------------
-- Increment or decrement the credits used on a topup
---------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  temba_insert_topupcredits(_topup_id INT, _count INT)
RETURNS VOID AS $$
BEGIN
  INSERT INTO orgs_topupcredits("topup_id", "used", "is_squashed") VALUES(_topup_id, _count, FALSE);
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):

    dependencies = [("orgs", "0030_install_triggers")]

    operations = [
        migrations.AddField(
            model_name="debit",
            name="is_squashed",
            field=models.BooleanField(default=False, help_text="Whether this row was created by squashing"),
        ),
        migrations.AddField(
            model_name="topupcredits",
            name="is_squashed",
            field=models.BooleanField(default=False, help_text="Whether this row was created by squashing"),
        ),
        migrations.RunSQL(SQL),
    ]
