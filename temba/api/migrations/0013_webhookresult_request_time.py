from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("api", "0012_auto_20170228_0837")]

    operations = [
        migrations.AddField(
            model_name="webhookresult",
            name="request_time",
            field=models.IntegerField(help_text="Time it took to process this request", null=True),
        )
    ]
