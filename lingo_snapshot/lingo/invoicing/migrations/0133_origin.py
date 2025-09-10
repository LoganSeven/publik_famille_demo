from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0132_payer_info'),
    ]

    operations = [
        migrations.AddField(
            model_name='credit',
            name='origin',
            field=models.CharField(
                choices=[('api', 'API'), ('basket', 'Basket'), ('campaign', 'Campaign')], null=True
            ),
        ),
        migrations.AddField(
            model_name='draftinvoice',
            name='origin',
            field=models.CharField(
                choices=[('api', 'API'), ('basket', 'Basket'), ('campaign', 'Campaign')], null=True
            ),
        ),
        migrations.AddField(
            model_name='invoice',
            name='origin',
            field=models.CharField(
                choices=[('api', 'API'), ('basket', 'Basket'), ('campaign', 'Campaign')], null=True
            ),
        ),
    ]
