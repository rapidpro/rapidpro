# Generated by Django 2.2.10 on 2020-12-01 19:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('migrator', '0008_migrationtask_start_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='migrationtask',
            name='end_date',
            field=models.DateField(null=True, verbose_name='End date'),
        ),
    ]
