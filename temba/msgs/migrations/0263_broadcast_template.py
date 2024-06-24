# Generated by Django 5.0.4 on 2024-06-21 20:43

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("msgs", "0262_add_msg_read_status"),
        ("templates", "0028_template_component_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="broadcast",
            name="template",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to="templates.template"),
        ),
    ]
