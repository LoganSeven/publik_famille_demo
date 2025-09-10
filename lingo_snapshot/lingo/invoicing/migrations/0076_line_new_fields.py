from django.conf import settings
from django.db import migrations
from django.utils import dateparse, formats, translation


def forward(apps, schema_editor):
    with translation.override(settings.LANGUAGE_CODE):
        DraftInvoiceLine = apps.get_model('invoicing', 'DraftInvoiceLine')
        InvoiceLine = apps.get_model('invoicing', 'InvoiceLine')
        Agenda = apps.get_model('agendas', 'Agenda')
        agendas_by_slug = {a.slug: a for a in Agenda.objects.all()}

        for line_model in [DraftInvoiceLine, InvoiceLine]:
            for line in line_model.objects.all():
                # init event_slug
                if line.details.get('agenda'):
                    line.event_slug = '%s@%s' % (line.details['agenda'], line.details['primary_event'])
                    line.event_label = line.label
                else:
                    line.event_slug = line.slug

                # init agenda_slug
                if '@' in line.event_slug:
                    line.agenda_slug = line.event_slug.split('@')[0]

                agenda = agendas_by_slug.get(line.agenda_slug)
                if agenda:
                    # init activity_label
                    line.activity_label = agenda.label

                    # init description
                    if line.details.get('dates') and not agenda.partial_bookings:
                        line.description = ', '.join(
                            formats.date_format(dateparse.parse_date(d), 'Dd') for d in line.details['dates']
                        )
                    # and fix partial_bookings
                    line.details['partial_bookings'] = agenda.partial_bookings

                # save line
                line.save()


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0075_line_new_fields'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
