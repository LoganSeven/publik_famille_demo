from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0041_payment_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='payer',
            name='payer_external_id_from_nameid_template',
            field=models.CharField(
                blank=True,
                help_text='{{ cards|objects:"adults"|filter_by_user:nameid|first|get:"id"|default:"" }}',
                max_length=1000,
                verbose_name='Template for payer external id from nameid',
            ),
        ),
    ]
