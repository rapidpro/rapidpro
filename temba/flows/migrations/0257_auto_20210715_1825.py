# Generated by Django 2.2.20 on 2021-07-15 18:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flows", "0256_auto_20210712_1723"),
    ]

    operations = [
        migrations.AlterField(
            model_name="flowcategorycount",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name="flowcategorycount",
            name="is_squashed",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="flownodecount",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name="flownodecount",
            name="is_squashed",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="flowpathcount",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name="flowpathcount",
            name="is_squashed",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="flowruncount",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name="flowruncount",
            name="is_squashed",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="flowstartcount",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name="flowstartcount",
            name="is_squashed",
            field=models.BooleanField(default=False),
        ),
    ]