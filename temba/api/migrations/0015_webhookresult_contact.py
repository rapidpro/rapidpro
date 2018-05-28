import django.db.models.deletion
from django.db import migrations, models


def backfill_webhook_result_contact(apps, schema_editor):
    WebHookResult = apps.get_model("api", "WebHookResult")
    for result in (
        WebHookResult.objects.filter(contact=None).exclude(event__run=None).select_related("event__run__contact")
    ):
        result.contact_id = result.event.run.contact_id
        result.save()


class Migration(migrations.Migration):

    atomic = False

    dependencies = [("contacts", "0067_auto_20170808_1852"), ("api", "0014_auto_20170410_0731")]

    operations = [
        # add nullable contact field
        migrations.AddField(
            model_name="webhookresult",
            name="contact",
            field=models.ForeignKey(
                help_text="The contact that generated this result",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="webhook_results",
                to="contacts.Contact",
            ),
        ),
        # backfill contact field
        migrations.RunPython(backfill_webhook_result_contact),
    ]
