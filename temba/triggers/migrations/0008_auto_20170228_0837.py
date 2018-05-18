from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('triggers', '0007_auto_20170117_2130'),
    ]

    operations = [
        migrations.AlterField(
            model_name='trigger',
            name='created_on',
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, help_text='When this item was originally created'),
        ),
        migrations.AlterField(
            model_name='trigger',
            name='modified_on',
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, help_text='When this item was last modified'),
        ),
    ]
