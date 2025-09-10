import django.core.serializers.json
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('invoicing', '0096_adjustment'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('agendas', '0007_negative_pricing_rate'),
        ('pricing', '0004_roles'),
        ('snapshot', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='RegieSnapshot',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('comment', models.TextField(blank=True, null=True)),
                (
                    'serialization',
                    models.JSONField(
                        blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder
                    ),
                ),
                ('label', models.CharField(blank=True, max_length=150, verbose_name='Label')),
                ('application_slug', models.CharField(max_length=100, null=True)),
                ('application_version', models.CharField(max_length=100, null=True)),
                (
                    'instance',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='instance_snapshots',
                        to='invoicing.regie',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                'ordering': ('-timestamp',),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='PricingSnapshot',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('comment', models.TextField(blank=True, null=True)),
                (
                    'serialization',
                    models.JSONField(
                        blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder
                    ),
                ),
                ('label', models.CharField(blank=True, max_length=150, verbose_name='Label')),
                ('application_slug', models.CharField(max_length=100, null=True)),
                ('application_version', models.CharField(max_length=100, null=True)),
                (
                    'instance',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='instance_snapshots',
                        to='pricing.pricing',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                'ordering': ('-timestamp',),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='PayerSnapshot',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('comment', models.TextField(blank=True, null=True)),
                (
                    'serialization',
                    models.JSONField(
                        blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder
                    ),
                ),
                ('label', models.CharField(blank=True, max_length=150, verbose_name='Label')),
                ('application_slug', models.CharField(max_length=100, null=True)),
                ('application_version', models.CharField(max_length=100, null=True)),
                (
                    'instance',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='instance_snapshots',
                        to='invoicing.payer',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                'ordering': ('-timestamp',),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CriteriaCategorySnapshot',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('comment', models.TextField(blank=True, null=True)),
                (
                    'serialization',
                    models.JSONField(
                        blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder
                    ),
                ),
                ('label', models.CharField(blank=True, max_length=150, verbose_name='Label')),
                ('application_slug', models.CharField(max_length=100, null=True)),
                ('application_version', models.CharField(max_length=100, null=True)),
                (
                    'instance',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='instance_snapshots',
                        to='pricing.criteriacategory',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                'ordering': ('-timestamp',),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CheckTypeGroupSnapshot',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('comment', models.TextField(blank=True, null=True)),
                (
                    'serialization',
                    models.JSONField(
                        blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder
                    ),
                ),
                ('label', models.CharField(blank=True, max_length=150, verbose_name='Label')),
                ('application_slug', models.CharField(max_length=100, null=True)),
                ('application_version', models.CharField(max_length=100, null=True)),
                (
                    'instance',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='instance_snapshots',
                        to='agendas.checktypegroup',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                'ordering': ('-timestamp',),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='AgendaSnapshot',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('comment', models.TextField(blank=True, null=True)),
                (
                    'serialization',
                    models.JSONField(
                        blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder
                    ),
                ),
                ('label', models.CharField(blank=True, max_length=150, verbose_name='Label')),
                ('application_slug', models.CharField(max_length=100, null=True)),
                ('application_version', models.CharField(max_length=100, null=True)),
                (
                    'instance',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='instance_snapshots',
                        to='agendas.agenda',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                'ordering': ('-timestamp',),
                'abstract': False,
            },
        ),
    ]
