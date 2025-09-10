from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('basket', '0011_date_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='basket',
            name='payer_email',
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name='basket',
            name='payer_phone',
            field=models.CharField(blank=True, max_length=250),
        ),
    ]
