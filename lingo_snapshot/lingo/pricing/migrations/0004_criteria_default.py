from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0003_extra_variables'),
    ]

    operations = [
        migrations.AddField(
            model_name='criteria',
            name='default',
            field=models.BooleanField(
                default=False,
                help_text='Will be applied if no other criteria matches',
                verbose_name='Default criteria',
            ),
        ),
        migrations.AlterField(
            model_name='criteria',
            name='condition',
            field=models.CharField(blank=True, max_length=1000, verbose_name='Condition'),
        ),
        migrations.AlterModelOptions(
            name='criteria',
            options={'ordering': ['default', 'order']},
        ),
    ]
