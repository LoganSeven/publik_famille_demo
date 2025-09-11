from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0003_auto_20150409_1840'),
    ]

    operations = [
        migrations.CreateModel(
            name='Service',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('name', models.CharField(max_length=128, verbose_name='name')),
                ('slug', models.SlugField(max_length=128, verbose_name='slug')),
            ],
            options={
                'base_manager_name': 'objects',
                'verbose_name': 'base service model',
                'verbose_name_plural': 'base service models',
            },
            bases=(models.Model,),
        ),
    ]
