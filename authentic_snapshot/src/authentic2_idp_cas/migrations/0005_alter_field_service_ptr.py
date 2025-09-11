from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_idp_cas', '0004_create_services'),
    ]

    operations = [
        migrations.AlterField(
            model_name='service',
            name='service_ptr',
            field=models.OneToOneField(to='authentic2.Service', on_delete=models.CASCADE),
            preserve_default=True,
        ),
    ]
