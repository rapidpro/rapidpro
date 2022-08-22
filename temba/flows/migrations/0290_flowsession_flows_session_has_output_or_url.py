# Generated by Django 4.0.4 on 2022-06-14 20:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flows", "0289_fail_ghost_runs"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="flowsession",
            constraint=models.CheckConstraint(
                check=models.Q(("output__isnull", False), ("output_url__isnull", False), _connector="OR"),
                name="flows_session_has_output_or_url",
            ),
        ),
    ]