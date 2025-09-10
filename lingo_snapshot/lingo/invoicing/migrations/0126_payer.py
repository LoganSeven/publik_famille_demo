from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0125_jobs'),
    ]

    operations = [
        migrations.AddField(
            model_name='regie',
            name='payer_cached_carddef_json',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='regie',
            name='payer_carddef_reference',
            field=models.CharField(blank=True, max_length=150, verbose_name='Card Model'),
        ),
        migrations.AddField(
            model_name='regie',
            name='payer_external_id_from_nameid_template',
            field=models.CharField(
                blank=True,
                help_text='Example of templated value: {{ cards|objects:"adults"|filter_by_user:nameid|first|get:"id"|default:"" }}',
                max_length=1000,
                verbose_name='Template for payer external id from nameid',
            ),
        ),
        migrations.AddField(
            model_name='regie',
            name='payer_external_id_prefix',
            field=models.CharField(blank=True, max_length=250, verbose_name='Prefix for payer external id'),
        ),
        migrations.AddField(
            model_name='regie',
            name='payer_external_id_template',
            field=models.CharField(
                blank=True,
                help_text='To get payer external id from user external id',
                max_length=1000,
                verbose_name='Template for payer external id',
            ),
        ),
        migrations.AddField(
            model_name='regie',
            name='payer_user_fields_mapping',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='regie',
            name='with_campaigns',
            field=models.BooleanField(default=False, verbose_name='Regie with invoicing campaigns'),
        ),
    ]
