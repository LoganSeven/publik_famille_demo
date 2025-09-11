from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0004_service'),
        ('saml', '0004_auto_20150410_1438'),
    ]

    operations = [
        migrations.AddField(
            model_name='libertyprovider',
            name='service_ptr',
            field=models.OneToOneField(null=True, to='authentic2.Service', on_delete=models.CASCADE),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='libertyprovider',
            name='name',
            field=models.CharField(
                help_text='Internal nickname for the service provider', max_length=140, null=True, blank=True
            ),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='libertyprovider',
            name='slug',
            field=models.SlugField(max_length=140, unique=True, null=True, blank=True),
            preserve_default=True,
        ),
    ]
