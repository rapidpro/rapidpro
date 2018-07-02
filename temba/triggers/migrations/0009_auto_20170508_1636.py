from django.db import migrations, models


def populate_trigger_match_types(apps, schema_editor):
    Trigger = apps.get_model("triggers", "Trigger")
    Trigger.objects.filter(trigger_type="K").update(match_type="F")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("triggers", "0008_auto_20170228_0837")]

    operations = [
        migrations.AddField(
            model_name="trigger",
            name="match_type",
            field=models.CharField(
                choices=[("F", "Message starts with keyword"), ("O", "Message only contains keyword")],
                default="F",
                help_text="How to match a message with a keyword",
                max_length=1,
                null=True,
                verbose_name="Trigger When",
            ),
        ),
        migrations.AlterField(
            model_name="trigger",
            name="keyword",
            field=models.CharField(
                blank=True,
                help_text="Word to match in the message text",
                max_length=16,
                null=True,
                verbose_name="Keyword",
            ),
        ),
        migrations.RunPython(populate_trigger_match_types),
    ]
