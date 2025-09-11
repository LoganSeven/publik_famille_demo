from django.db import migrations, models

import authentic2.saml.fields


class Migration(migrations.Migration):
    dependencies = [
        ('saml', '0014_auto_20150617_1216'),
    ]

    operations = [
        migrations.AlterField(
            model_name='idpoptionssppolicy',
            name='requested_name_id_format',
            field=models.CharField(
                default='none',
                max_length=200,
                verbose_name='Requested NameID format',
                choices=[
                    ('username', 'Username (use with Google Apps)'),
                    ('none', 'None'),
                    ('uuid', 'UUID'),
                    ('persistent', 'Persistent'),
                    ('transient', 'Transient'),
                    ('edupersontargetedid', 'Use eduPersonTargetedID attribute'),
                    ('email', 'Email'),
                ],
            ),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='spoptionsidppolicy',
            name='accepted_name_id_format',
            field=authentic2.saml.fields.MultiSelectField(
                blank=True,
                choices=[
                    ('none', 'None'),
                    ('persistent', 'Persistent'),
                    ('transient', 'Transient'),
                    ('email', 'Email'),
                    ('username', 'Username (use with Google Apps)'),
                    ('uuid', 'UUID'),
                    ('edupersontargetedid', 'Use eduPersonTargetedID attribute'),
                ],
                max_length=1024,
                verbose_name='NameID formats accepted',
            ),
        ),
        migrations.AlterField(
            model_name='spoptionsidppolicy',
            name='default_name_id_format',
            field=models.CharField(
                default='none',
                max_length=256,
                choices=[
                    ('none', 'None'),
                    ('persistent', 'Persistent'),
                    ('transient', 'Transient'),
                    ('email', 'Email'),
                    ('username', 'Username (use with Google Apps)'),
                    ('uuid', 'UUID'),
                    ('edupersontargetedid', 'Use eduPersonTargetedID attribute'),
                ],
            ),
        ),
    ]
