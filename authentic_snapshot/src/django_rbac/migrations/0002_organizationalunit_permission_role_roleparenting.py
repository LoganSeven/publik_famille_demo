from django.conf import settings
from django.db import migrations, models

import authentic2.a2_rbac


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        migrations.swappable_dependency(settings.RBAC_OU_MODEL),
        migrations.swappable_dependency(settings.RBAC_PERMISSION_MODEL),
        migrations.swappable_dependency(settings.RBAC_ROLE_MODEL),
        migrations.swappable_dependency(settings.RBAC_ROLE_PARENTING_MODEL),
        ('contenttypes', '0001_initial'),
        ('django_rbac', '0001_initial'),
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
                        default=authentic2.a2_rbac.utils.get_hex_uuid,
                        unique=True,
                        max_length=32,
                        verbose_name='uuid',
                    ),
                ),
                ('name', models.CharField(max_length=256, verbose_name='name')),
                ('slug', models.SlugField(max_length=256, verbose_name='slug')),
                ('description', models.TextField(verbose_name='description', blank=True)),
            ],
            options={
                'swappable': 'RBAC_OU_MODEL',
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
                'swappable': 'RBAC_PERMISSION_MODEL',
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
                        default=authentic2.a2_rbac.utils.get_hex_uuid,
                        unique=True,
                        max_length=32,
                        verbose_name='uuid',
                    ),
                ),
                ('name', models.CharField(max_length=256, verbose_name='name')),
                ('slug', models.SlugField(max_length=256, verbose_name='slug')),
                ('description', models.TextField(verbose_name='description', blank=True)),
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
            ],
            options={
                'swappable': 'RBAC_ROLE_MODEL',
                'verbose_name': 'role',
                'verbose_name_plural': 'roles',
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
                'swappable': 'RBAC_ROLE_PARENTING_MODEL',
                'verbose_name': 'role parenting relation',
                'verbose_name_plural': 'role parenting relations',
            },
            bases=(models.Model,),
        ),
    ]
