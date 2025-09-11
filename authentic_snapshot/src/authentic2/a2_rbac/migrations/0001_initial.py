from django.conf import settings
from django.db import migrations, models

import authentic2.a2_rbac.fields
import authentic2.utils.misc


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0004_service'),
        ('django_rbac', '__first__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contenttypes', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrganizationalUnit',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                (
                    'uuid',
                    models.CharField(
                        default=authentic2.utils.misc.get_hex_uuid,
                        unique=True,
                        max_length=32,
                        verbose_name='uuid',
                    ),
                ),
                ('name', models.CharField(max_length=256, verbose_name='name')),
                ('slug', models.SlugField(max_length=256, verbose_name='slug')),
                ('description', models.TextField(verbose_name='description', blank=True)),
                (
                    'default',
                    authentic2.a2_rbac.fields.UniqueBooleanField(verbose_name='Default organizational unit'),
                ),
            ],
            options={
                'verbose_name': 'organizational unit',
                'verbose_name_plural': 'organizational units',
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='Permission',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('target_id', models.PositiveIntegerField()),
                (
                    'operation',
                    models.ForeignKey(
                        verbose_name='operation', to='django_rbac.Operation', on_delete=models.CASCADE
                    ),
                ),
                (
                    'ou',
                    models.ForeignKey(
                        related_name='scoped_permission',
                        verbose_name='organizational unit',
                        to=settings.RBAC_OU_MODEL,
                        null=True,
                        on_delete=models.CASCADE,
                    ),
                ),
                (
                    'target_ct',
                    models.ForeignKey(
                        related_name='+', to='contenttypes.ContentType', on_delete=models.CASCADE
                    ),
                ),
            ],
            options={
                'verbose_name': 'permission',
                'verbose_name_plural': 'permissions',
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='Role',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                (
                    'uuid',
                    models.CharField(
                        default=authentic2.utils.misc.get_hex_uuid,
                        unique=True,
                        max_length=32,
                        verbose_name='uuid',
                    ),
                ),
                ('name', models.CharField(max_length=256, verbose_name='name')),
                ('slug', models.SlugField(max_length=256, verbose_name='slug')),
                ('description', models.TextField(verbose_name='description', blank=True)),
                (
                    'admin_scope_id',
                    models.PositiveIntegerField(
                        null=True, verbose_name='administrative scope id', blank=True
                    ),
                ),
                (
                    'admin_scope_ct',
                    models.ForeignKey(
                        verbose_name='administrative scope content type',
                        blank=True,
                        to='contenttypes.ContentType',
                        null=True,
                        on_delete=models.CASCADE,
                    ),
                ),
                (
                    'members',
                    models.ManyToManyField(related_name='roles', to=settings.AUTH_USER_MODEL, blank=True),
                ),
                (
                    'ou',
                    models.ForeignKey(
                        verbose_name='organizational unit',
                        blank=True,
                        to=settings.RBAC_OU_MODEL,
                        null=True,
                        on_delete=models.CASCADE,
                    ),
                ),
                (
                    'permissions',
                    models.ManyToManyField(
                        related_name='role', to=settings.RBAC_PERMISSION_MODEL, blank=True
                    ),
                ),
                (
                    'service',
                    models.ForeignKey(
                        verbose_name='service',
                        blank=True,
                        to='authentic2.Service',
                        null=True,
                        on_delete=models.CASCADE,
                    ),
                ),
            ],
            options={
                'ordering': ('ou', 'service', 'name'),
                'verbose_name': 'role',
                'verbose_name_plural': 'roles',
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='RoleAttribute',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('name', models.CharField(max_length=64, verbose_name='name')),
                (
                    'kind',
                    models.CharField(max_length=32, verbose_name='kind', choices=[('string', 'string')]),
                ),
                ('value', models.TextField(verbose_name='value')),
                (
                    'role',
                    models.ForeignKey(
                        related_name='attributes',
                        verbose_name='role',
                        to=settings.RBAC_ROLE_MODEL,
                        on_delete=models.CASCADE,
                    ),
                ),
            ],
            options={
                'verbose_name': 'role attribute',
                'verbose_name_plural': 'role attributes',
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='RoleParenting',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('direct', models.BooleanField(blank=True, default=True)),
                (
                    'child',
                    models.ForeignKey(
                        related_name='parent_relation', to=settings.RBAC_ROLE_MODEL, on_delete=models.CASCADE
                    ),
                ),
                (
                    'parent',
                    models.ForeignKey(
                        related_name='child_relation', to=settings.RBAC_ROLE_MODEL, on_delete=models.CASCADE
                    ),
                ),
            ],
            options={
                'verbose_name': 'role parenting relation',
                'verbose_name_plural': 'role parenting relations',
            },
            bases=(models.Model,),
        ),
        migrations.AlterUniqueTogether(
            name='organizationalunit',
            unique_together={('name',), ('slug',)},
        ),
        migrations.AlterUniqueTogether(
            name='roleattribute',
            unique_together={('role', 'name', 'kind', 'value')},
        ),
        migrations.AlterUniqueTogether(
            name='role',
            unique_together={('slug', 'service'), ('slug', 'admin_scope_ct', 'admin_scope_id', 'ou')},
        ),
    ]
