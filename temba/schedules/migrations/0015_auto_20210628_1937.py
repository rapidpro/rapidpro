# Generated by Django 2.2.20 on 2021-06-28 19:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("schedules", "0014_squashed"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="schedule",
            index=models.Index(
                condition=models.Q(is_active=True, next_fire__isnull=False),
                fields=["next_fire"],
                name="schedules_next_fire_active",
            ),
        ),
    ]
