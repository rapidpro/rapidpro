from django.db import migrations


class Migration(migrations.Migration):

    atomic = False

    dependencies = [("api", "0015_webhookresult_contact")]

    operations = [
        migrations.RunSQL(
            """
            CREATE INDEX CONCURRENTLY api_webhookevent_next_attempt_errored_non_flow
            ON api_webhookevent(next_attempt)
            WHERE status = 'E' AND event != 'flow';
        """
        ),
        migrations.RunSQL(
            """
            CREATE INDEX CONCURRENTLY api_webhookevent_created_on_pending_non_flow
            ON api_webhookevent(created_on)
            WHERE status = 'P' AND event != 'flow';
        """
        ),
        migrations.RunSQL(
            """
            CREATE INDEX CONCURRENTLY api_webhookevent_modified_on_errored_non_flow
            ON api_webhookevent(modified_on)
            WHERE status = 'E' AND event != 'flow';
        """
        ),
    ]
