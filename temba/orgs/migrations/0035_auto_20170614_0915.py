from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0034_auto_20170228_0837'),
    ]

    operations = [
        migrations.AlterField(
            model_name='debit',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
        migrations.AlterField(
            model_name='topupcredits',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
    ]
