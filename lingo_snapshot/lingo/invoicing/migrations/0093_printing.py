from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0092_roles'),
    ]

    operations = [
        migrations.RenameField(
            model_name='regie',
            old_name='cashier_name',
            new_name='controller_name',
        ),
        migrations.RenameField(
            model_name='regie',
            old_name='invoice_main_colour',
            new_name='main_colour',
        ),
        migrations.AddField(
            model_name='regie',
            name='certificate_model',
            field=models.CharField(
                choices=[('basic', 'Basic'), ('middle', 'Middle'), ('full', 'Full')],
                blank=True,
                max_length=10,
                verbose_name='Payments certificate model',
            ),
        ),
        migrations.AlterField(
            model_name='regie',
            name='controller_name',
            field=models.CharField(blank=True, max_length=256, verbose_name='Controller name'),
        ),
        migrations.AlterField(
            model_name='regie',
            name='main_colour',
            field=models.CharField(default='#DF5A13', max_length=7, verbose_name='Main colour in documents'),
        ),
    ]
