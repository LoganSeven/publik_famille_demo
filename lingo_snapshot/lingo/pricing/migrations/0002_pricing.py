import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('agendas', '0001_initial'),
        ('pricing', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Criteria',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=150, verbose_name='Label')),
                ('slug', models.SlugField(max_length=160, verbose_name='Identifier')),
                ('condition', models.CharField(max_length=1000, verbose_name='Condition')),
                ('order', models.PositiveIntegerField()),
            ],
            options={
                'ordering': ['order'],
            },
        ),
        migrations.CreateModel(
            name='CriteriaCategory',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=150, verbose_name='Label')),
                ('slug', models.SlugField(max_length=160, unique=True, verbose_name='Identifier')),
            ],
            options={
                'ordering': ['label'],
            },
        ),
        migrations.CreateModel(
            name='Pricing',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('label', models.CharField(max_length=150, verbose_name='Label')),
                ('slug', models.SlugField(max_length=160, unique=True, verbose_name='Identifier')),
            ],
            options={
                'ordering': ['label'],
            },
        ),
        migrations.CreateModel(
            name='PricingCriteriaCategory',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('order', models.PositiveIntegerField()),
                (
                    'category',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to='pricing.CriteriaCategory'
                    ),
                ),
                (
                    'pricing',
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='pricing.Pricing'),
                ),
            ],
            options={
                'ordering': ['order'],
                'unique_together': {('pricing', 'category')},
            },
        ),
        migrations.AddField(
            model_name='pricing',
            name='categories',
            field=models.ManyToManyField(
                related_name='pricings',
                through='pricing.PricingCriteriaCategory',
                to='pricing.CriteriaCategory',
            ),
        ),
        migrations.AddField(
            model_name='pricing',
            name='criterias',
            field=models.ManyToManyField(to='pricing.Criteria'),
        ),
        migrations.AddField(
            model_name='criteria',
            name='category',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='criterias',
                to='pricing.CriteriaCategory',
                verbose_name='Category',
            ),
        ),
        migrations.CreateModel(
            name='AgendaPricing',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('date_start', models.DateField()),
                ('date_end', models.DateField()),
                ('pricing_data', models.JSONField(null=True)),
                (
                    'agenda',
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='agendas.Agenda'),
                ),
                (
                    'pricing',
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='pricing.Pricing'),
                ),
            ],
        ),
        migrations.AlterUniqueTogether(
            name='criteria',
            unique_together={('category', 'slug')},
        ),
    ]
