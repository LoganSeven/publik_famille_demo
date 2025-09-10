from django.db import migrations


def forward(apps, schema_editor):
    Regie = apps.get_model('invoicing', 'Regie')
    for regie in Regie.objects.all():
        if regie.payer:
            regie.payer_carddef_reference = regie.payer.carddef_reference
            regie.payer_cached_carddef_json = regie.payer.cached_carddef_json
            regie.payer_external_id_prefix = regie.payer.payer_external_id_prefix
            regie.payer_external_id_template = regie.payer.payer_external_id_template
            regie.payer_external_id_from_nameid_template = regie.payer.payer_external_id_from_nameid_template
            regie.payer_user_fields_mapping = regie.payer.user_fields_mapping
            regie.with_campaigns = True
            regie.save()


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0126_payer'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
