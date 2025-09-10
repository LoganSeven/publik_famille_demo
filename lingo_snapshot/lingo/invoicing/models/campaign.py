# lingo - payment and billing system
# Copyright (C) 2025  Entr'ouvert
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import copy
import sys
import traceback
import uuid
from itertools import islice

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils.formats import date_format
from django.utils.timezone import localtime, now
from django.utils.translation import gettext_lazy as _

from lingo.agendas.chrono import ChronoError, lock_events_check
from lingo.agendas.models import Agenda
from lingo.invoicing import errors
from lingo.invoicing.models.base import DOCUMENT_MODELS
from lingo.pricing import errors as pricing_errors
from lingo.utils.fields import RichTextField
from lingo.utils.misc import shorten_slug


class Campaign(models.Model):
    label = models.CharField(_('Label'), max_length=150)
    regie = models.ForeignKey('invoicing.Regie', on_delete=models.PROTECT)
    date_start = models.DateField(_('Start date'))
    date_end = models.DateField(_('End date'))
    date_publication = models.DateField(
        _('Publication date'), help_text=_('Date on which invoices are visible on the portal.')
    )
    date_payment_deadline_displayed = models.DateField(
        _('Displayed payment deadline'),
        help_text=_(
            'Payment deadline displayed to user on the portal. Leave empty to display the effective payment deadline.'
        ),
        null=True,
        blank=True,
    )
    date_payment_deadline = models.DateField(
        _('Effective payment deadline'), help_text=_('Date on which invoices are no longer payable online.')
    )
    date_due = models.DateField(
        _('Due date'), help_text=_('Date on which invoices are no longer payable at the counter.')
    )
    date_debit = models.DateField(_('Debit date'))
    injected_lines = models.CharField(
        _('Integrate injected lines'),
        choices=[
            ('no', _('no')),
            ('period', _('yes, only for the period')),
            ('all', _('yes, all injected lines before the end of the period')),
        ],
        default='no',
        max_length=10,
    )
    adjustment_campaign = models.BooleanField(_('Adjustment campaign'), default=False)
    agendas = models.ManyToManyField(Agenda, related_name='campaigns')
    invalid = models.BooleanField(default=False)
    finalized = models.BooleanField(default=False)

    invoice_model = models.CharField(
        _('Invoice model'),
        max_length=10,
        choices=DOCUMENT_MODELS,
        default='middle',
    )
    invoice_custom_text = RichTextField(
        _('Custom text in invoice'),
        blank=True,
        null=True,
        help_text=_('Displayed under the address and additional information blocks.'),
    )

    primary_campaign = models.ForeignKey(
        'self', null=True, on_delete=models.PROTECT, related_name='corrective_campaigns'
    )

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def __str__(self):
        return _('%(label)s (%(start)s - %(end)s)') % {
            'label': self.label,
            'start': date_format(self.date_start, 'd/m/Y'),
            'end': date_format(self.date_end, 'd/m/Y'),
        }

    @property
    def is_last(self):
        if self.primary_campaign_id is None:
            if not Campaign.objects.filter(primary_campaign=self).exists():
                return True
            return False
        return not Campaign.objects.filter(
            primary_campaign=self.primary_campaign, created_at__gt=self.created_at
        ).exists()

    def mark_as_valid(self):
        self.invalid = False
        self.save()

    def mark_as_invalid(self, commit=True):
        self.invalid = True
        if commit:
            self.save()

    def mark_as_finalized(self):
        self.finalized = True
        self.save()
        return self.assign_credits()

    def generate(self):
        pool = self.pool_set.create(draft=True)
        try:
            pool.init()
        except Exception:
            return
        job = CampaignAsyncJob.objects.create(
            campaign=self,
            action='generate',
            params={
                'draft_pool_id': pool.pk,
                'force_cron': bool('uwsgi' in sys.modules),
            },
        )
        job.run()
        return job

    def assign_credits(self):
        job = CampaignAsyncJob.objects.create(
            campaign=self,
            action='assign_credits',
        )
        job.run()
        return job

    def make_assignments(self, job=None):
        from lingo.invoicing.models import Credit, Invoice

        invoices_qs = list(
            Invoice.objects.filter(
                pool__campaign=self,
                date_due__gte=now().date(),
                remaining_amount__gt=0,
            ).order_by('pk')
        )
        credits_qs = list(
            Credit.objects.filter(
                pool__campaign=self,
                remaining_amount__gt=0,
            ).order_by('pk')
        )
        if job:
            job.set_total_count(len(invoices_qs + credits_qs))

        # assign generated credits to existing invoices
        for invoice in invoices_qs:
            invoice.make_assignments()
            if job:
                job.increment_count()
        # assign existing credits to generated invoices
        for credit in credits_qs:
            credit.make_assignments()
            if job:
                job.increment_count()

    def get_agenda_unlock_logs(self):
        if self.primary_campaign:
            return self.primary_campaign.get_agenda_unlock_logs()
        return self.agendaunlocklog_set.filter(agenda__in=self.agendas.all(), active=True).select_related(
            'agenda'
        )


class Pool(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT)
    draft = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)
    completed_at = models.DateTimeField(null=True)
    status = models.CharField(
        choices=[
            ('registered', _('Registered')),
            ('running', _('Running')),
            ('failed', _('Failed')),
            ('completed', _('Completed')),
        ],
        default='registered',
        max_length=100,
    )
    exception = models.TextField()

    @property
    def is_last(self):
        return not self.campaign.pool_set.filter(created_at__gt=self.created_at).exists()

    def init(self):
        from lingo.invoicing import utils

        try:
            agendas = utils.get_agendas(pool=self)
            if agendas:
                lock_events_check(
                    agenda_slugs=[a.slug for a in agendas],
                    date_start=self.campaign.date_start,
                    date_end=self.campaign.date_end,
                )
        except ChronoError as e:
            self.status = 'failed'
            self.exception = e.msg
            self.completed_at = now()
            self.save()
            raise
        except Exception:
            self.status = 'failed'
            self.exception = traceback.format_exc()
            self.completed_at = now()
            self.save()
            raise

    def prepare_invoice_generation(self):
        from lingo.invoicing import utils

        self.status = 'running'
        self.save()
        try:
            # get agendas with pricing corresponding to the period
            agendas = utils.get_agendas(pool=self)
            # get subscribed users for each agenda, for the period
            return utils.get_users_from_subscriptions(agendas=agendas, pool=self)
        except ChronoError as e:
            self.status = 'failed'
            self.exception = e.msg
            raise
        except Exception:
            self.status = 'failed'
            self.exception = traceback.format_exc()
            raise
        finally:
            self.save()

    def generate_invoices(self, users, job=None):
        from lingo.invoicing import utils

        try:
            # get agendas with pricing corresponding to the period
            agendas = utils.get_agendas(pool=self)
            # build journal lines for all subscribed users, for each agenda in the corresponding period
            utils.build_lines_for_users(agendas=agendas, users=users, pool=self, job=job)
        except ChronoError as e:
            self.status = 'failed'
            self.exception = e.msg
            raise
        except Exception:
            self.status = 'failed'
            self.exception = traceback.format_exc()
            raise
        finally:
            self.save()

    def finalize_invoice_generation(self, job=None):
        from lingo.invoicing import utils

        try:
            utils.generate_invoices_from_lines(pool=self, job=job)
        except Exception:
            self.status = 'failed'
            self.exception = traceback.format_exc()
            raise
        finally:
            if self.status == 'running':
                self.status = 'completed'
            self.completed_at = now()
            self.save()

    def promote(self):
        if not self.is_last:
            # not the last
            raise errors.PoolPromotionError('Pool too old')
        if not self.draft:
            # not a draft
            raise errors.PoolPromotionError('Pool is final')
        if self.status != 'completed':
            # not completed
            raise errors.PoolPromotionError('Pool is not completed')

        final_pool = copy.deepcopy(self)
        final_pool.pk = None
        final_pool.draft = False
        final_pool.status = 'registered'
        final_pool.completed_at = None
        final_pool.save()

        job = CampaignAsyncJob.objects.create(
            campaign=self.campaign,
            action='populate_from_draft',
            params={'draft_pool_id': self.pk, 'final_pool_id': final_pool.pk},
        )
        job.run()
        return job

    def populate_from_draft(self, draft_pool, job=None):
        if job:
            total_count = draft_pool.draftjournalline_set.count() + draft_pool.draftinvoice_set.count()
            job.set_total_count(total_count)
        try:
            self.status = 'running'
            self.save()

            batch_size = 1000

            # generate journal lines in the same order as drafts, by batch
            lines = draft_pool.draftjournalline_set.order_by('pk').iterator(chunk_size=batch_size)
            journal_line_mapping = {}
            while True:
                batch = list(islice(lines, batch_size))
                if not batch:
                    break
                final_lines = []
                for line in batch:
                    final_line = line.promote(pool=self, bulk=True)
                    final_lines.append(final_line)
                # bulk create journal lines
                JournalLine.objects.bulk_create(final_lines, batch_size)
                # keep a mapping draft line -> final line
                journal_line_mapping.update({jl._original_line.pk: jl.pk for jl in final_lines})
                if job:
                    job.increment_count(amount=len(final_lines))

            # now create invoices and credits, and update journal lines to set invoice_line/credit_line FKs
            for invoice in draft_pool.draftinvoice_set.order_by('pk').iterator(chunk_size=batch_size):
                invoice.promote(pool=self, journal_line_mapping=journal_line_mapping)
                if job:
                    job.increment_count()

        except Exception:
            self.status = 'failed'
            self.exception = traceback.format_exc()
            raise
        finally:
            if self.status == 'running':
                self.status = 'completed'
            self.completed_at = now()
            self.save()


class InjectedLine(models.Model):
    event_date = models.DateField()
    slug = models.SlugField(max_length=1000)
    label = models.TextField()
    amount = models.DecimalField(max_digits=9, decimal_places=2)

    user_external_id = models.CharField(max_length=250)
    payer_external_id = models.CharField(max_length=250)
    payer_first_name = models.CharField(max_length=250)
    payer_last_name = models.CharField(max_length=250)
    payer_address = models.TextField()
    payer_direct_debit = models.BooleanField(default=False)
    regie = models.ForeignKey('invoicing.Regie', on_delete=models.PROTECT)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def save(self, *args, **kwargs):
        shorten_slug(self)
        super().save(*args, **kwargs)


class AbstractJournalLine(models.Model):
    event_date = models.DateField()
    slug = models.SlugField(max_length=1000)
    label = models.TextField()
    description = models.TextField()
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    quantity = models.IntegerField(default=1)
    quantity_type = models.CharField(
        max_length=10,
        choices=[
            ('units', _('Units')),
            ('minutes', _('Minutes')),
        ],
        default='units',
    )
    accounting_code = models.CharField(max_length=250, blank=True)

    user_external_id = models.CharField(max_length=250)
    user_first_name = models.CharField(max_length=250)
    user_last_name = models.CharField(max_length=250)
    payer_external_id = models.CharField(max_length=250)
    payer_first_name = models.CharField(max_length=250)
    payer_last_name = models.CharField(max_length=250)
    payer_address = models.TextField()
    payer_email = models.CharField(max_length=250, blank=True)
    payer_phone = models.CharField(max_length=250, blank=True)
    payer_direct_debit = models.BooleanField(default=False)
    event = models.JSONField(default=dict)
    booking = models.JSONField(default=dict)
    pricing_data = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    status = models.CharField(
        max_length=10,
        choices=[
            ('success', _('Success')),
            ('warning', _('Warning')),
            ('error', _('Error')),
        ],
    )
    error_status = models.CharField(
        max_length=10,
        choices=[
            ('ignored', _('Ignored')),
            ('fixed', _('Fixed')),
        ],
        blank=True,
    )

    pool = models.ForeignKey(Pool, on_delete=models.PROTECT, null=True)
    from_injected_line = models.ForeignKey(InjectedLine, on_delete=models.PROTECT, null=True)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        abstract = True

    @property
    def user_name(self):
        user_name = '%s %s' % (self.user_first_name, self.user_last_name)
        return user_name.strip()

    @property
    def payer_name(self):
        payer_name = '%s %s' % (self.payer_first_name, self.payer_last_name)
        return payer_name.strip()

    @staticmethod
    def get_error_class(error_type):
        error_classes = {
            'PricingNotFound': pricing_errors.PricingNotFound,
            'CriteriaConditionNotFound': pricing_errors.CriteriaConditionNotFound,
            'MultipleDefaultCriteriaCondition': pricing_errors.MultipleDefaultCriteriaCondition,
            'PricingDataError': pricing_errors.PricingDataError,
            'PricingDataFormatError': pricing_errors.PricingDataFormatError,
            'MinPricingDataError': pricing_errors.MinPricingDataError,
            'MinPricingDataFormatError': pricing_errors.MinPricingDataFormatError,
            'PricingReductionRateError': pricing_errors.PricingReductionRateError,
            'PricingReductionRateFormatError': pricing_errors.PricingReductionRateFormatError,
            'PricingReductionRateValueError': pricing_errors.PricingReductionRateValueError,
            'PricingEffortRateTargetError': pricing_errors.PricingEffortRateTargetError,
            'PricingEffortRateTargetFormatError': pricing_errors.PricingEffortRateTargetFormatError,
            'PricingEffortRateTargetValueError': pricing_errors.PricingEffortRateTargetValueError,
            'PricingAccountingCodeError': pricing_errors.PricingAccountingCodeError,
            'PricingUnknownCheckStatusError': pricing_errors.PricingUnknownCheckStatusError,
            'PricingEventNotCheckedError': pricing_errors.PricingEventNotCheckedError,
            'PricingBookingNotCheckedError': pricing_errors.PricingBookingNotCheckedError,
            'PricingMultipleBookingError': pricing_errors.PricingMultipleBookingError,
            'PricingBookingCheckTypeError': pricing_errors.PricingBookingCheckTypeError,
            'PayerError': errors.PayerError,
            'PayerDataError': errors.PayerDataError,
        }
        return error_classes.get(error_type)

    @staticmethod
    def get_error_label(error_type):
        error_class = AbstractJournalLine.get_error_class(error_type)
        if error_class is None:
            return error_type
        return error_class.label

    def get_error_display(self):
        if self.status == 'success':
            return
        error = str(self.pricing_data.get('error'))
        error_class = AbstractJournalLine.get_error_class(error)
        if error_class is None:
            return error
        error_details = self.pricing_data.get('error_details', {})
        error = error_class(details=error_details)
        return error.get_error_display()

    def get_chrono_event_url(self):
        if not settings.KNOWN_SERVICES.get('chrono'):
            return
        chrono = list(settings.KNOWN_SERVICES['chrono'].values())[0]
        chrono_url = chrono.get('url')
        if not chrono_url:
            return
        if not self.event.get('agenda') or not self.event.get('slug'):
            return
        return '%smanage/agendas/%s/events/%s/' % (chrono_url, self.event['agenda'], self.event['slug'])

    def save(self, *args, **kwargs):
        shorten_slug(self)
        super().save(*args, **kwargs)


class DraftJournalLine(AbstractJournalLine):
    invoice_line = models.ForeignKey(
        'invoicing.DraftInvoiceLine', on_delete=models.PROTECT, null=True, related_name='journal_lines'
    )

    class Meta:
        indexes = [
            models.Index(fields=['pool', 'status']),
            models.Index(
                fields=['pool_id'],
                condition=models.Q(pricing_data__error__isnull=False),
                name='invoicing_djl_error_idx',
            ),
        ]

    def promote(self, pool=None, invoice_line=None, credit_line=None, bulk=False):
        final_line = copy.deepcopy(self)
        final_line.__class__ = JournalLine
        final_line.pk = None
        final_line.pool = pool
        final_line.invoice_line = invoice_line
        final_line.credit_line = credit_line
        final_line.error_status = ''
        if not bulk:
            final_line.save()
        final_line._original_line = self
        return final_line


class JournalLine(AbstractJournalLine):
    invoice_line = models.ForeignKey(
        'invoicing.InvoiceLine', on_delete=models.PROTECT, null=True, related_name='journal_lines'
    )
    credit_line = models.ForeignKey(
        'invoicing.CreditLine', on_delete=models.PROTECT, null=True, related_name='journal_lines'
    )

    class Meta:
        indexes = [
            models.Index(fields=['pool', 'status', 'error_status']),
            models.Index(
                fields=['pool_id'],
                condition=models.Q(pricing_data__error__isnull=False),
                name='invoicing_jl_error_idx',
            ),
        ]


STATUS_CHOICES = [
    ('registered', _('Registered')),
    ('waiting', _('Waiting')),
    ('running', _('Running')),
    ('failed', _('Failed')),
    ('completed', _('Completed')),
]


class AbstractAsyncJob(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, primary_key=True)
    status = models.CharField(
        max_length=100,
        default='registered',
        choices=STATUS_CHOICES,
    )
    exception = models.TextField()
    action = models.CharField(max_length=100)
    params = models.JSONField(default=dict)
    total_count = models.PositiveIntegerField(default=0)
    current_count = models.PositiveIntegerField(default=0)
    failure_label = models.TextField(blank=True)
    result_data = models.JSONField(default=dict)

    creation_timestamp = models.DateTimeField(auto_now_add=True)
    last_update_timestamp = models.DateTimeField(auto_now=True)
    completion_timestamp = models.DateTimeField(default=None, null=True)

    class Meta:
        abstract = True

    def __str__(self):
        return str(
            _('Job "%(label)s" started on %(date)s')
            % {
                'label': self.label,
                'date': date_format(localtime(self.creation_timestamp), 'DATETIME_FORMAT'),
            }
        )

    @property
    def label(self):
        return self.status

    @property
    def is_ready(self):
        return True

    def set_total_count(self, num):
        self.total_count = num
        self.save(update_fields=['total_count'])

    def increment_count(self, amount=1):
        self.current_count = (self.current_count or 0) + amount
        if (now() - self.last_update_timestamp).total_seconds() > 1 or self.current_count >= self.total_count:
            self.save(update_fields=['current_count'])

    def get_completion_status(self):
        return {
            'job': self,
            'progression_status': self.get_progression(),
        }

    def get_progression(self):
        current_count = self.current_count or 0

        if not current_count:
            return ''

        if not self.total_count:
            return _('%(current_count)s (unknown total)') % {'current_count': current_count}

        return _('%(current_count)s/%(total_count)s (%(percent)s%%)') % {
            'current_count': int(current_count),
            'total_count': self.total_count,
            'percent': int(current_count * 100 / self.total_count),
        }

    def run(self, cron=True):
        if cron:
            if 'uwsgi' in sys.modules:
                return
            if self.params.get('force_cron') is True:
                return
        self.status = 'running'
        self.save()
        try:
            getattr(self, self.action)()
        except errors.WaitForOtherJobs:
            self.status = 'waiting'
        except errors.AsyncJobException as e:
            self.status = 'failed'
            self.exception = str(e)
            self.failure_label = str(_('Error: %s') % str(e))
        except Exception:
            self.status = 'failed'
            self.exception = traceback.format_exc()
        finally:
            if self.status == 'running':
                self.status = 'completed'
            if self.status in ['completed', 'failed']:
                self.completion_timestamp = now()
            self.save()


class CampaignAsyncJob(AbstractAsyncJob):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE)

    @property
    def label(self):
        return {
            'generate': _('Invoices generation preparation'),
            'assign_credits': _('Campaign validation'),
            'populate_from_draft': _('Invoices generation'),
        }.get(self.action)

    def check_completion(self):
        jobs = [self] + list(self.poolasyncjob_set.all().order_by('creation_timestamp'))
        return {
            'job_progression': [j.get_completion_status() for j in jobs],
            'all_jobs_completed': all(j.status == 'completed' for j in jobs),
            'any_job_failed': any(j.status == 'failed' for j in jobs),
        }

    def generate(self):
        try:
            draft_pool = self.campaign.pool_set.get(pk=self.params['draft_pool_id'], draft=True)
        except Pool.DoesNotExist:
            raise errors.AsyncJobException('draft pool not found')
        if draft_pool.status != 'registered':
            raise errors.AsyncJobException('pool wrong status %s (wanted: registered)' % draft_pool.status)

        jobs_num = settings.POOL_JOBS_PER_CAMPAIGN
        self.set_total_count(1 + jobs_num + 1)

        users = draft_pool.prepare_invoice_generation()
        self.increment_count()

        splitted_users = []
        user_keys = list(sorted(users.keys()))
        for i in range(jobs_num):
            subuser_keys = user_keys[i::jobs_num]
            splitted_users.append({k: users[k] for k in subuser_keys})

        for users_sublist in splitted_users:
            if not users_sublist:
                self.increment_count()
                continue
            job = PoolAsyncJob.objects.create(
                pool=draft_pool,
                campaign_job=self,
                action='generate_invoices',
                users=users_sublist,
                params={'force_cron': self.params.get('force_cron', False)},
            )
            job.run()
            self.increment_count()

        job = PoolAsyncJob.objects.create(
            pool=draft_pool,
            campaign_job=self,
            action='finalize_invoices',
            params={'force_cron': self.params.get('force_cron', False)},
        )
        job.run()
        self.increment_count()

    def assign_credits(self):
        if not self.campaign.finalized:
            raise errors.AsyncJobException('campaign not finalized')
        self.campaign.make_assignments(job=self)

    def populate_from_draft(self):
        try:
            draft_pool = self.campaign.pool_set.get(pk=self.params['draft_pool_id'], draft=True)
        except Pool.DoesNotExist:
            raise errors.AsyncJobException('draft pool not found')
        if draft_pool.status != 'completed':
            raise errors.AsyncJobException('pool wrong status %s (wanted: completed)' % draft_pool.status)

        if self.campaign.pool_set.filter(created_at__gt=draft_pool.created_at, draft=True).exists():
            raise errors.AsyncJobException('more recent draft pool exists')

        try:
            final_pool = self.campaign.pool_set.get(pk=self.params['final_pool_id'], draft=False)
        except Pool.DoesNotExist:
            raise errors.AsyncJobException('final pool not found')
        if final_pool.status != 'registered':
            raise errors.AsyncJobException(
                'final pool wrong status %s (wanted: registered)' % final_pool.status
            )
        final_pool.populate_from_draft(draft_pool=draft_pool, job=self)


class PoolAsyncJob(AbstractAsyncJob):
    pool = models.ForeignKey(Pool, on_delete=models.CASCADE)
    users = models.JSONField(default=dict)
    campaign_job = models.ForeignKey(CampaignAsyncJob, null=True, on_delete=models.SET_NULL)

    @property
    def label(self):
        return {
            'generate_invoices': _('Invoice lines generation'),
            'finalize_invoices': _('Invoices finalization'),
        }.get(self.action)

    @property
    def is_ready(self):
        if self.action == 'finalize_invoices':
            jobs = PoolAsyncJob.objects.filter(
                pool=self.pool, campaign_job=self.campaign_job, action='generate_invoices'
            ).all()
            if not jobs:
                return True
            if any(j.status == 'failed' for j in jobs):
                return True
            if all(j.status == 'completed' for j in jobs):
                return True
            return False
        return True

    def generate_invoices(self):
        if not self.pool.draft:
            raise errors.AsyncJobException('pool is not draft')
        if self.pool.status != 'running':
            raise errors.AsyncJobException('pool wrong status %s (wanted: running)' % self.pool.status)

        self.pool.generate_invoices(users=self.users or {}, job=self)

    def finalize_invoices(self):
        if not self.pool.draft:
            raise errors.AsyncJobException('pool is not draft')
        if self.pool.status != 'running':
            raise errors.AsyncJobException('pool wrong status %s (wanted: running)' % self.pool.status)

        jobs = PoolAsyncJob.objects.filter(
            pool=self.pool, campaign_job=self.campaign_job, action='generate_invoices'
        ).all()
        for job in jobs:
            if job.status == 'failed':
                # normally pool should be in status "failed", so this case should not happen
                raise errors.AsyncJobException('a pool job has failed, stop campaign')
            if job.status != 'completed':
                raise errors.WaitForOtherJobs()

        self.pool.finalize_invoice_generation(job=self)
