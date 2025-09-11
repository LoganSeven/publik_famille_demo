from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Nonce',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('value', models.CharField(max_length=256)),
                ('context', models.CharField(max_length=256, null=True, blank=True)),
                ('not_on_or_after', models.DateTimeField(null=True, blank=True)),
            ],
            options={},
            bases=(models.Model,),
        ),
    ]
