# Generated by Django 2.2.4 on 2020-04-15 19:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("orgs", "0058_auto_20190723_2129")]

    operations = [
        migrations.AddField(
            model_name="usersettings",
            name="authy_id",
            field=models.CharField(blank=True, max_length=255, null=True, verbose_name="Authy ID"),
        )
    ]