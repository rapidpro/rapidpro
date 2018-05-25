from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [("campaigns", "0016_remove_campaignevent_message")]

    operations = [
        migrations.AlterField(
            model_name="campaignevent",
            name="flow",
            field=models.ForeignKey(
                help_text="The flow that will be triggered",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="events",
                to="flows.Flow",
            ),
        )
    ]
