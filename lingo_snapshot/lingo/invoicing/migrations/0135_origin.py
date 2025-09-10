from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0134_origin'),
    ]

    operations = [
        migrations.AlterField(
            model_name='credit',
            name='origin',
            field=models.CharField(choices=[('api', 'API'), ('basket', 'Basket'), ('campaign', 'Campaign')]),
        ),
        migrations.AlterField(
            model_name='draftinvoice',
            name='origin',
            field=models.CharField(choices=[('api', 'API'), ('basket', 'Basket'), ('campaign', 'Campaign')]),
        ),
        migrations.AlterField(
            model_name='invoice',
            name='origin',
            field=models.CharField(choices=[('api', 'API'), ('basket', 'Basket'), ('campaign', 'Campaign')]),
        ),
    ]
