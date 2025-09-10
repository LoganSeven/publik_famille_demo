# w.c.s. - web application for online forms
# Copyright (C) 2005-2021  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import io
import json
import tarfile
import xml.etree.ElementTree as ET

from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse
from quixote import get_publisher

from wcs.api_utils import is_url_signed
from wcs.applications import Application, ApplicationElement
from wcs.blocks import BlockDef, BlockdefImportError
from wcs.carddata import ApplicationCardData
from wcs.carddef import CardDef
from wcs.categories import (
    BlockCategory,
    CardDefCategory,
    Category,
    CommentTemplateCategory,
    DataSourceCategory,
    MailTemplateCategory,
    WorkflowCategory,
)
from wcs.comment_templates import CommentTemplate
from wcs.data_sources import NamedDataSource, NamedDataSourceImportError
from wcs.formdef import FormDef
from wcs.formdef_base import FormdefImportError
from wcs.mail_templates import MailTemplate
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import Contains, Equal, Role, TestUser
from wcs.workflows import Workflow, WorkflowImportError
from wcs.wscalls import NamedWsCall, NamedWsCallImportError

from .qommon import _
from .qommon.afterjobs import AfterJob
from .qommon.misc import xml_node_text

klasses = {
    'blocks': BlockDef,
    'blocks-categories': BlockCategory,
    'cards': CardDef,
    'cards-categories': CardDefCategory,
    'cards-data': ApplicationCardData,
    'data-sources': NamedDataSource,
    'data-sources-categories': DataSourceCategory,
    'forms-categories': Category,
    'forms': FormDef,
    'roles': Role,
    'mail-templates-categories': MailTemplateCategory,
    'mail-templates': MailTemplate,
    'comment-templates-categories': CommentTemplateCategory,
    'comment-templates': CommentTemplate,
    'workflows-categories': WorkflowCategory,
    'workflows': Workflow,
    'wscalls': NamedWsCall,
    'users': TestUser,
}

klasses_with_install_only_option = {
    'blocks',
    'cards',
    'cards-data',
    'data-sources',
    'forms',
    'mail-templates',
    'comment-templates',
    'workflows',
    'wscalls',
}

klass_to_slug = {y: x for x, y in klasses.items()}

category_classes = [
    Category,
    CardDefCategory,
    BlockCategory,
    WorkflowCategory,
    MailTemplateCategory,
    CommentTemplateCategory,
    DataSourceCategory,
]


def signature_required(func):
    def f(*args, **kwargs):
        if not is_url_signed():
            return HttpResponseForbidden()
        return func(*args, **kwargs)

    return f


@signature_required
def index(request):
    response = [
        {'id': 'forms', 'text': _('Forms'), 'singular': _('Form')},
        {'id': 'cards', 'text': _('Cards'), 'singular': _('Card')},
        {
            'id': 'cards-data',
            'text': _('Cards data'),
            'singular': _('Cards data'),
            'minor': not get_publisher().has_site_option('enable-carddata-applification'),
        },
        {'id': 'workflows', 'text': _('Workflows'), 'singular': _('Workflow')},
        {'id': 'blocks', 'text': _('Blocks'), 'singular': _('Block of fields'), 'minor': True},
        {'id': 'data-sources', 'text': _('Data Sources'), 'singular': _('Data Source'), 'minor': True},
        {'id': 'mail-templates', 'text': _('Mail Templates'), 'singular': _('Mail Template'), 'minor': True},
        {
            'id': 'comment-templates',
            'text': _('Comment Templates'),
            'singular': _('Comment Template'),
            'minor': True,
        },
        {'id': 'wscalls', 'text': _('Webservice Calls'), 'singular': _('Webservice Call'), 'minor': True},
        {
            'id': 'blocks-categories',
            'text': _('Categories (blocks)'),
            'singular': _('Category (block)'),
            'minor': True,
        },
        {
            'id': 'cards-categories',
            'text': _('Categories (cards)'),
            'singular': _('Category (cards)'),
            'minor': True,
        },
        {
            'id': 'forms-categories',
            'text': _('Categories (forms)'),
            'singular': _('Category (forms)'),
            'minor': True,
        },
        {
            'id': 'workflows-categories',
            'text': _('Categories (workflows)'),
            'singular': _('Category (workflows)'),
            'minor': True,
        },
        {
            'id': 'mail-templates-categories',
            'text': _('Categories (mail templates)'),
            'singular': _('Category (mail templates)'),
            'minor': True,
        },
        {
            'id': 'comment-templates-categories',
            'text': _('Categories (comment templates)'),
            'singular': _('Category (comment templates)'),
            'minor': True,
        },
        {
            'id': 'data-sources-categories',
            'text': _('Categories (data sources)'),
            'singular': _('Category (data Sources)'),
            'minor': True,
        },
        {
            'id': 'roles',
            'text': _('Roles'),
            'singular': _('Role'),
            'minor': True,
        },
        {'id': 'users', 'text': _('Test users'), 'singular': _('Test user'), 'minor': True},
    ]
    for obj in response:
        obj['urls'] = {
            'list': request.build_absolute_uri(
                reverse('api-export-import-objects-list', kwargs={'objects': obj['id']})
            ),
        }
        if obj['id'] in klasses_with_install_only_option:
            help_text = _(
                'This element will not be updated if it already exists on the instance where the application is deployed.'
            )
            if obj['id'] == 'cards-data':
                help_text = _(
                    'If at least one card data already exists for this card on the instance where '
                    'the application is deployed, the cards data will not be imported.'
                )
            obj['config_options'] = [
                {
                    'varname': 'install_only',
                    'field_type': 'bool',
                    'label': _('Installation only'),
                    'help_text': help_text,
                    'default_value': False,
                },
            ]
    return JsonResponse({'data': response})


def export_object_ref(request, obj):
    slug = obj.slug
    objects = klass_to_slug[obj.__class__]
    try:
        urls = {
            'export': request.build_absolute_uri(
                reverse('api-export-import-object-export', kwargs={'objects': objects, 'slug': slug})
            ),
            'dependencies': request.build_absolute_uri(
                reverse('api-export-import-object-dependencies', kwargs={'objects': objects, 'slug': slug})
            ),
        }
    except NoReverseMatch:
        return None
    urls.update(
        {
            'redirect': request.build_absolute_uri(
                reverse('api-export-import-object-redirect', kwargs={'objects': objects, 'slug': slug})
            )
        }
    )
    data = {
        'id': slug,
        'text': obj.name,
        'type': objects,
        'urls': urls,
    }
    if hasattr(obj, 'category_id'):
        data['category'] = obj.category.name if (obj.category_id and obj.category) else None
    if objects == 'roles':
        # include uuid in object reference, this is not used for applification API but is useful
        # for authentic creating its role summary page.
        data['uuid'] = obj.uuid
        data['urls'] = {}
    return data


@signature_required
def objects_list(request, objects):
    klass = klasses.get(objects)
    if not klass:
        raise Http404()
    object_refs = [export_object_ref(request, x) for x in klass.select()]
    return JsonResponse({'data': [x for x in object_refs if x]})


def get_object(objects, slug):
    klass = klasses.get(objects)
    if not klass:
        raise Http404()
    return klass.get_by_slug(slug, ignore_errors=True)


@signature_required
def object_export(request, objects, slug):
    obj = get_object(objects, slug)
    if obj is None:
        raise Http404()
    if hasattr(obj, 'export_for_application'):
        content, content_type = obj.export_for_application()
    else:
        etree = obj.export_to_xml(include_id=True)
        ET.indent(etree)
        content = ET.tostring(etree)
        content_type = 'text/xml'
    return HttpResponse(content, content_type=content_type)


def object_redirect(request, objects, slug):
    obj = get_object(objects, slug)
    if obj is None or objects == 'roles':
        raise Http404()
    url = obj.get_admin_url()
    if (
        'compare' in request.GET
        and request.GET.get('application')
        and request.GET.get('version1')
        and request.GET.get('version2')
    ):
        url += 'history/compare?version1=%s&version2=%s&application=%s' % (
            request.GET['version1'],
            request.GET['version2'],
            request.GET['application'],
        )
    return redirect(url)


@signature_required
def object_dependencies(request, objects, slug):
    obj = get_object(objects, slug)
    if obj is None:
        raise Http404()
    dependencies = []
    if hasattr(obj, 'get_dependencies'):
        for dependency in obj.get_dependencies():
            if dependency is None:
                continue
            object_ref = export_object_ref(request, dependency)
            if object_ref:
                dependencies.append(object_ref)
    return JsonResponse({'data': dependencies})


class BundleKeyError(Exception):
    pass


class BundleCheckJob(AfterJob):
    def __init__(self, tar_content, params, **kwargs):
        super().__init__(**kwargs)
        self.tar_content_file = PicklableUpload('app.tar', 'application/x-tar')
        self.tar_content_file.receive([tar_content])
        self.params = params

    def execute(self):
        object_types = [x for x in klasses if x != 'roles']

        base_url = get_publisher().get_frontoffice_url()
        error = None
        differences = []
        unknown_elements = []
        no_history_elements = []
        legacy_elements = []
        uninstalled_elements = []
        try:
            with (
                io.BytesIO(self.tar_content_file.get_content()) as tar_io,
                tarfile.open(fileobj=tar_io) as tar,
            ):
                try:
                    manifest = json.loads(tar.extractfile('manifest.json').read().decode())
                except KeyError:
                    raise BundleKeyError(_('Invalid tar file, missing manifest.'))
                application_slug = manifest.get('slug')
                application_version = manifest.get('version_number')
                if not application_slug:
                    raise BundleKeyError(_('Invalid tar file, missing application.'))
                if not application_version:
                    raise BundleKeyError(_('Invalid tar file, missing version.'))

                config_options = manifest.get('config_options') or {}
                install_only = [k for k, v in config_options.get('install_only', {}).items() if v is True]
                elements_from_next_bundle = getattr(self, 'params', {}).get('elements_from_next_bundle')

                # count number of actions
                self.total_count = len([x for x in manifest.get('elements') if x.get('type') in object_types])

                for element in manifest.get('elements'):
                    component_key = '%s/%s' % (element['type'], element['slug'])
                    if element['type'] not in klasses or element['type'] == 'roles':
                        continue
                    if component_key in install_only and element['type'] in klasses_with_install_only_option:
                        # don't check components declared for install only
                        self.increment_count()
                        continue
                    element_klass = klasses[element['type']]
                    if element_klass is ApplicationCardData:
                        self.increment_count()
                        continue
                    if component_key not in elements_from_next_bundle:
                        # element is not referenced in next bundle, it will be uninstalled.
                        # don't check differences
                        uninstalled_elements.append(
                            {
                                'type': element['type'],
                                'slug': element['slug'],
                            }
                        )
                        self.increment_count()
                        continue
                    try:
                        element_content = tar.extractfile(component_key).read()
                    except KeyError:
                        raise BundleKeyError(_('Invalid tar file, missing component %s' % component_key))
                    tree = ET.fromstring(element_content)
                    if hasattr(element_klass, 'url_name'):
                        slug = xml_node_text(tree.find('url_name'))
                    elif hasattr(element_klass, 'test_uuid'):
                        slug = xml_node_text(tree.find('test_uuid'))
                    else:
                        slug = xml_node_text(tree.find('slug'))
                    try:
                        obj = element_klass.get_by_slug(slug)
                        if obj is None:
                            raise KeyError
                    except KeyError:
                        # element not found, report this as unexisting
                        unknown_elements.append(
                            {
                                'type': element['type'],
                                'slug': element['slug'],
                            }
                        )
                        self.increment_count()
                        continue
                    applications = Application.select([Equal('slug', application_slug)])
                    legacy = False
                    if not applications:
                        legacy = True
                    else:
                        application = applications[0]
                        elements = ApplicationElement.select(
                            [
                                Equal('application_id', application.id),
                                Equal('object_type', obj.xml_root_node),
                                Equal('object_id', str(obj.id)),
                            ]
                        )
                        if not elements:
                            legacy = True
                    if legacy:
                        # object exists, but not linked to the application
                        legacy_elements.append(
                            {
                                'type': element['type'],
                                'slug': element['slug'],
                                # information needed here, Relation objects may not exist yet in hobo
                                'text': obj.name,
                                'url': '%s%s'
                                % (
                                    base_url,
                                    reverse(
                                        'api-export-import-object-redirect',
                                        kwargs={'objects': element['type'], 'slug': element['slug']},
                                    ),
                                ),
                            }
                        )
                        self.increment_count()
                        continue
                    snapshots_for_app = get_publisher().snapshot_class.select(
                        [
                            Equal('object_type', obj.xml_root_node),
                            Equal('object_id', str(obj.id)),
                            Equal('application_slug', application_slug),
                            Equal('application_version', application_version),
                        ],
                        order_by='-timestamp',
                    )
                    if not snapshots_for_app:
                        # legacy, no snapshot for this bundle
                        no_history_elements.append(
                            {
                                'type': element['type'],
                                'slug': element['slug'],
                            }
                        )
                        self.increment_count()
                        continue
                    snapshot_for_app = snapshots_for_app[0]
                    last_snapshot = get_publisher().snapshot_class.select_object_history(
                        obj, [Equal('application_ignore_change', False)]
                    )[0]
                    if snapshot_for_app.id != last_snapshot.id:
                        differences.append(
                            {
                                'type': element['type'],
                                'slug': element['slug'],
                                'url': '%shistory/compare?version1=%s&version2=%s'
                                % (obj.get_admin_url(), snapshot_for_app.id, last_snapshot.id),
                            }
                        )
                    self.increment_count()
        except tarfile.TarError:
            error = _('Invalid tar file.')
        except BundleKeyError as e:
            error = str(e)

        if error:
            self.status = 'failed'
            self.failure_label = str(_('Error: %s') % error)
        else:
            self.result_data = {
                'differences': differences,
                'unknown_elements': unknown_elements,
                'no_history_elements': no_history_elements,
                'legacy_elements': legacy_elements,
                'uninstalled_elements': uninstalled_elements,
            }
        self.store()


@signature_required
def bundle_check(request):
    try:
        elements_from_next_bundle = json.loads(request.POST.get('elements_from_next_bundle'))
    except (TypeError, json.JSONDecodeError):
        elements_from_next_bundle = []
    params = {'elements_from_next_bundle': elements_from_next_bundle}
    job = BundleCheckJob(tar_content=request.FILES['bundle'].read(), params=params)
    job.store()
    job.run(spool=True)
    return JsonResponse({'err': 0, 'url': job.get_api_status_url()})


class BundleImportJob(AfterJob):
    def __init__(self, tar_content, **kwargs):
        super().__init__(**kwargs)
        self.tar_content_file = PicklableUpload('app.tar', 'application/x-tar')
        self.tar_content_file.receive([tar_content])

    def __getstate__(self):
        odict = self.__dict__.copy()
        odict.pop('tar', None)
        return odict

    def execute(self):
        timing = self.start_timing('bundle import job')
        object_types = [x for x in klasses if x != 'roles']

        def order_key(x):
            # be sure categories are imported first
            if 'categories' in x:
                return 0
            # then comment & mail templates
            if 'templates' in x:
                return 1
            # then blocks, then workflows, then the rest
            return {'blocks': 2, 'workflows': 3}.get(x, 1000)

        object_types = sorted(object_types, key=order_key)

        error = None
        try:
            self.add_timing_mark('open tarfile')
            with (
                io.BytesIO(self.tar_content_file.get_content()) as tar_io,
                tarfile.open(fileobj=tar_io) as self.tar,
            ):
                try:
                    self.add_timing_mark('load manifest')
                    manifest = json.loads(self.tar.extractfile('manifest.json').read().decode())
                except KeyError:
                    raise BundleKeyError(_('Invalid tar file, missing manifest.'))
                self.add_timing_mark('update or create application')
                self.application = Application.update_or_create_from_manifest(
                    manifest, self.tar, editable=False, install=False
                )
                config_options = manifest.get('config_options') or {}
                self.install_only = [
                    k for k, v in config_options.get('install_only', {}).items() if v is True
                ]

                # count number of actions
                self.total_count = 0
                self.total_count += len(
                    [
                        x
                        for x in manifest.get('elements')
                        if x.get('type') in ('forms', 'cards', 'blocks', 'workflows')
                    ]
                )
                self.total_count += (
                    len(
                        [
                            x
                            for x in manifest.get('elements')
                            if x.get('type') in object_types and x.get('type') != 'cards-data'
                        ]
                    )
                    * 2
                )
                self.total_count += len(
                    [x for x in manifest.get('elements') if x.get('type') == 'cards-data']
                )

                # init cache of application elements, from imported manifest
                self.application_elements = set()
                self.application_created_elements = set()

                # first pass on formdef/carddef/blockdef/workflows to create them empty
                # (name and slug); so they can be found for sure in import pass
                for _type in ('forms', 'cards', 'blocks', 'workflows'):
                    self.add_timing_mark(f'pre-install {_type}')
                    self.pre_install([x for x in manifest.get('elements') if x.get('type') == _type])
                    self.add_timing_mark(f'pre-install {_type} -- done')

                # real installation pass
                for _type in object_types:
                    self.add_timing_mark(f'install {_type}')
                    self.install([x for x in manifest.get('elements') if x.get('type') == _type])
                    self.add_timing_mark(f'install {_type} -- done')

                # again, to remove [pre-install] in dependencies labels
                for _type in object_types:
                    if _type == 'cards-data':
                        continue
                    self.add_timing_mark(f'install(2) {_type}')
                    self.install(
                        [x for x in manifest.get('elements') if x.get('type') == _type], finalize=True
                    )
                    self.add_timing_mark(f'install(2) {_type} -- done')

                # remove obsolete application elements
                self.add_timing_mark('unlink obsolete objects')
                self.unlink_obsolete_objects()

        except (
            BlockdefImportError,
            FormdefImportError,
            WorkflowImportError,
            NamedDataSourceImportError,
            NamedWsCallImportError,
        ) as e:
            error = str(e)
            if getattr(e, 'details', None):
                error += ' (%s)' % e.details
        except tarfile.TarError:
            error = _('Invalid tar file.')
        except BundleKeyError as e:
            error = str(e)

        self.stop_timing(timing)
        if error:
            self.mark_as_failed(_('Error: %s') % error)
        else:
            self.store()

    def pre_install(self, elements):
        for element in elements:
            component_key = '%s/%s' % (element['type'], element['slug'])
            self.add_timing_mark(f'pre_install {component_key}')
            element_klass = klasses[element['type']]
            try:
                element_content = self.tar.extractfile(component_key).read()
            except KeyError:
                raise BundleKeyError(_('Invalid tar file, missing component %s.') % component_key)
            tree = ET.fromstring(element_content)
            if hasattr(element_klass, 'url_name'):
                slug = xml_node_text(tree.find('url_name'))
            else:
                slug = xml_node_text(tree.find('slug'))
            try:
                existing_object = element_klass.get_by_slug(slug)
            except KeyError:
                pass
            else:
                if existing_object:
                    self.increment_count()
                    continue
            new_object = element_klass()
            new_object.slug = slug
            new_object.name = '[pre-import] %s' % xml_node_text(tree.find('name'))
            new_object.store(comment=_('Application (%s) initial installation') % self.application.name)
            self.application_created_elements.add(component_key)
            self.link_object(new_object)
            self.increment_count()

        # process pre-import after jobs earlier, so there are no multiple jobs for
        # the same object afterwards.
        get_publisher().process_after_jobs(spool=False)

    def install(self, elements, finalize=False):
        if not elements:
            return

        element_klass = klasses[elements[0]['type']]

        if not finalize and element_klass in category_classes:
            # for categories, keep positions before install
            objects_by_slug = {i.slug: i for i in element_klass.select()}
            initial_positions = {
                i.slug: i.position if i.position is not None else 10000 for i in objects_by_slug.values()
            }

        imported_positions = {}
        updated_formdefs = set()

        for element in elements:
            component_key = '%s/%s' % (element['type'], element['slug'])
            self.add_timing_mark(f'install {component_key}')
            try:
                element_content = self.tar.extractfile(component_key).read()
            except KeyError:
                raise BundleKeyError(_('Invalid tar file, missing component %s.') % component_key)
            if element_klass is ApplicationCardData:
                element = ApplicationCardData.get_by_slug(element['slug'])
                # if at least one carddata exists and install_only option is set, do not update data
                if component_key in self.install_only:
                    if element.carddef.data_class().count():
                        self.increment_count()
                        continue
                element.import_from_file(element_content)
                self.increment_count()
                continue
            new_object = element_klass.import_from_xml_tree(
                ET.fromstring(element_content),
                include_id=False,
                check_datasources=False,
                check_deprecated=True,
            )
            if not finalize and element_klass in category_classes:
                # for categories, keep positions of imported objects
                imported_positions[new_object.slug] = (
                    new_object.position if new_object.position is not None else 10000
                )
            try:
                existing_object = element_klass.get_by_slug(new_object.slug)
                if existing_object is None:
                    raise KeyError()
            except KeyError:
                new_object.store(
                    comment=_('Application (%s)') % self.application.name, application=self.application
                )
                if hasattr(new_object, 'store_for_application'):
                    new_object.store_for_application()
                self.link_object(new_object)
                self.increment_count()
                continue

            # if object already exists and install_only option is available and set, do not update it
            if component_key in self.install_only and element['type'] in klasses_with_install_only_option:
                if component_key not in self.application_created_elements:
                    self.increment_count()
                    continue

            # replace
            new_object.id = existing_object.id
            if element['type'] == 'workflows':
                # keep track of status remappings
                new_object.status_remapping_done = getattr(existing_object, 'status_remapping_done', set())
            if finalize:
                last_snapshot = get_publisher().snapshot_class.get_latest(
                    new_object.xml_root_node, new_object.id
                )

                if element['type'] == 'workflows' and new_object.status_remapping:
                    # remap status
                    from wcs.admin.workflows import StatusChangeJob

                    self.add_timing_mark('run status change job')
                    ran_remapping = False
                    for old_status_id, remapping in new_object.status_remapping.items():
                        remapping_identifier = '%(action)s_%(status)s_%(timestamp)s' % remapping
                        if remapping_identifier in new_object.status_remapping_done:
                            # skip remapping that has already been run
                            continue
                        new_object.status_remapping_done.add(remapping_identifier)
                        job = StatusChangeJob(
                            new_object.id,
                            action=remapping.get('action'),
                            current_status=f'wf-{old_status_id}',
                        )
                        ran_remapping = True
                        job.id = job.DO_NOT_STORE
                        job.execute()
                    self.add_timing_mark('run status change job -- done')
                    if ran_remapping:
                        comment = _('Application (%s) workflow status migration')
                        new_object.store(
                            comment=comment % self.application.name,
                            application=self.application,
                            migration_update=True,
                        )

                if '[pre-import]' not in last_snapshot.get_serialization():
                    self.increment_count()
                    continue

            last_app_snapshot = get_publisher().snapshot_class.get_latest(
                new_object.xml_root_node, new_object.id, application=self.application
            )

            if element['type'].endswith('-categories'):
                # keep role settings when updating
                for attr in ('export_roles', 'statistics_roles', 'management_roles'):
                    setattr(new_object, attr, getattr(existing_object, attr))

            if element['type'] in ('blocks', 'forms', 'cards') and not existing_object.name.startswith(
                '[pre-import]'
            ):
                updated_formdefs.add(new_object)

            if element['type'] in ('forms', 'cards') and not existing_object.name.startswith('[pre-import]'):
                # keep some settings when updating
                for attr in (
                    'workflow_options',
                    'workflow_roles',
                    'roles',
                    'required_authentication_contexts',
                    'backoffice_submission_roles',
                    'publication_date',
                    'expiration_date',
                    'disabled',
                    'disabled_redirection',
                ):
                    setattr(new_object, attr, getattr(existing_object, attr))

                # keep internal references
                new_object.table_name = existing_object.table_name

                # remove shared views, they will be recreated when the object is stored
                for custom_view in get_publisher().custom_view_class.select_shared_for_formdef(new_object):
                    # do not call remove_self() to avoid storing formdef repeatedly
                    get_publisher().custom_view_class.remove_object(custom_view.id)

                if (
                    existing_object.workflow.slug != new_object.workflow.slug
                    and new_object.workflow_migrations
                ):
                    # workflow change
                    migration_key = f'{existing_object.workflow.slug} {new_object.workflow.slug}'
                    migration = new_object.workflow_migrations.get(migration_key)
                    if migration:
                        # run workflow change on the existing formdef, that has a reference
                        # to the old workflow.
                        self.add_timing_mark('run workflow change')
                        existing_object.change_workflow(
                            new_object.workflow,
                            migration['status_mapping'],
                            snapshot_comment=_('Application (%s) update, workflow change')
                            % self.application.name,
                        )
                        self.add_timing_mark('run workflow change -- done')
                        # workflow change may have updated geolocations, record it here
                        # (instead of relying on the workflow being stored again and
                        # triggering this).
                        new_object.geolocations = existing_object.geolocations

            comment = _('Application (%s) update')
            if existing_object.name.startswith('[pre-import]'):
                comment = _('Application (%s) complete initial installation')
            if finalize:
                comment = _('Application (%s) finalize initial installation')
            new_object.store(comment=comment % self.application.name, application=self.application)

            if element['type'] in ('forms', 'cards', 'blocks') and last_app_snapshot:
                # reapply local changes
                last_app_snapshot._check_datasources = False
                reapplied_local_changes = False
                if existing_object.name != last_app_snapshot.instance.name:
                    new_object.name = existing_object.name
                    reapplied_local_changes = True

                snapshot_fields_by_id = {x.id: x for x in last_app_snapshot.instance.fields}
                new_fields_by_id = {x.id: x for x in new_object.fields or []}
                for field in existing_object.fields or []:
                    if field.id in snapshot_fields_by_id and field.id in new_fields_by_id:
                        for attrname in ('required', 'label'):
                            if getattr(field, attrname, None) != getattr(
                                snapshot_fields_by_id[field.id], attrname, None
                            ):
                                setattr(new_fields_by_id[field.id], attrname, getattr(field, attrname, None))
                                reapplied_local_changes = True

                if reapplied_local_changes:
                    new_object.store(
                        comment=_('Application (%s) update, re-applying local changes')
                        % self.application.name,
                        application_ignore_change=True,
                    )

            if hasattr(new_object, 'store_for_application'):
                new_object.store_for_application()
            self.link_object(new_object)
            self.increment_count()

        # for categories, rebuild positions
        if not finalize and element_klass in category_classes:
            objects_by_slug = {i.slug: i for i in element_klass.select()}
            # find imported objects from initials
            existing_positions = {k: v for k, v in initial_positions.items() if k in imported_positions}
            # find not imported objects from initials
            not_imported_positions = {
                k: v for k, v in initial_positions.items() if k not in imported_positions
            }
            # determine position of application objects
            application_position = None
            if existing_positions:
                application_position = min(existing_positions.values())
            # all objects placed before application objects
            before_positions = {
                k: v
                for k, v in not_imported_positions.items()
                if application_position is None or v < application_position
            }
            # all objects placed after application objects
            after_positions = {
                k: v
                for k, v in not_imported_positions.items()
                if application_position is not None and v >= application_position
            }
            # rebuild positions
            position = 1
            slugs = sorted(before_positions.keys(), key=lambda a: before_positions[a])
            slugs += sorted(imported_positions.keys(), key=lambda a: imported_positions[a])
            slugs += sorted(after_positions.keys(), key=lambda a: after_positions[a])
            for slug in slugs:
                objects_by_slug[slug].position = position
                objects_by_slug[slug].store(store_snapshot=False)
                position += 1

        # for updated carddefs/formdefs, rebuild digests and statistics
        if updated_formdefs:
            from wcs.formdef_jobs import UpdateDigestsAndStatisticsDataAfterJob

            updated_carddefs_formdefs = set()
            for obj in updated_formdefs:
                if isinstance(obj, (CardDef, FormDef)):
                    updated_carddefs_formdefs.add(obj)
                elif isinstance(obj, BlockDef):
                    updated_carddefs_formdefs.update(
                        {x for x in obj.get_usage_formdefs() if isinstance(x, (CardDef, FormDef))}
                    )

            job = UpdateDigestsAndStatisticsDataAfterJob(formdefs=updated_carddefs_formdefs)
            get_publisher().add_after_job(job, force_async=True)

    def link_object(self, obj):
        if isinstance(obj, ApplicationCardData):
            return
        element = ApplicationElement.update_or_create_for_object(self.application, obj)
        self.application_elements.add((element.object_type, str(element.object_id)))

    def unlink_obsolete_objects(self):
        known_elements = ApplicationElement.select([Equal('application_id', self.application.id)])
        for element in known_elements:
            if (element.object_type, element.object_id) not in self.application_elements:
                ApplicationElement.remove_object(element.id)


@signature_required
def bundle_import(request):
    job = BundleImportJob(tar_content=request.FILES['bundle'].read())
    job.store()
    job.run(spool=True)
    return JsonResponse({'err': 0, 'url': job.get_api_status_url()})


class BundleDeclareJob(BundleImportJob):
    def execute(self):
        object_types = [x for x in klasses if x != 'roles']

        error = None
        try:
            with (
                io.BytesIO(self.tar_content_file.get_content()) as tar_io,
                tarfile.open(fileobj=tar_io) as self.tar,
            ):
                try:
                    manifest = json.loads(self.tar.extractfile('manifest.json').read().decode())
                except KeyError:
                    raise BundleKeyError(_('Invalid tar file, missing manifest.'))

                self.application = Application.update_or_create_from_manifest(
                    manifest, self.tar, editable=True, install=True
                )

                # count number of actions
                self.total_count = len([x for x in manifest.get('elements') if x.get('type') in object_types])

                # init cache of application elements, from manifest
                self.application_elements = set()

                # declare elements
                for type in object_types:
                    self.declare([x for x in manifest.get('elements') if x.get('type') == type])

                # remove obsolete application elements
                self.unlink_obsolete_objects()
        except tarfile.TarError:
            error = _('Invalid tar file.')
        except BundleKeyError as e:
            error = str(e)

        if error:
            self.status = 'failed'
            self.mark_as_failed(_('Error: %s') % error)
        else:
            self.store()

    def declare(self, elements):
        for element in elements:
            element_klass = klasses[element['type']]
            element_slug = element['slug']
            existing_object = element_klass.get_by_slug(element_slug, ignore_errors=True)
            if existing_object:
                self.link_object(existing_object)
            self.increment_count()


@signature_required
def bundle_declare(request):
    job = BundleDeclareJob(tar_content=request.FILES['bundle'].read())
    job.store()
    job.run(spool=True)
    return JsonResponse({'err': 0, 'url': job.get_api_status_url()})


@signature_required
def unlink(request):
    if request.method == 'POST' and request.POST.get('application'):
        applications = Application.select([Equal('slug', request.POST['application'])])
        if applications:
            application = applications[0]
            elements = ApplicationElement.select([Equal('application_id', application.id)])
            for element in elements:
                ApplicationElement.remove_object(element.id)
            Application.remove_object(application.id)

    return JsonResponse({'err': 0})


@signature_required
def uninstall_check(request):
    if request.method == 'POST' and request.POST.get('application'):
        applications = Application.select([Equal('slug', request.POST['application'])])
        if applications:
            application = applications[0]
            elements = ApplicationElement.select(
                [Equal('application_id', application.id), Contains('object_type', ['formdef', 'carddef'])]
            )
            for element in elements:
                object_klass = get_publisher().get_object_class(element.object_type)
                formdef_object = object_klass.get(element.object_id, ignore_errors=True)
                if formdef_object and formdef_object.data_class().exists():
                    return JsonResponse(
                        {'err': 1, 'err_desc': _('Existing data in "%s"') % formdef_object.name}
                    )
    return JsonResponse({'err': 0})


@signature_required
def uninstall(request):
    if request.method == 'POST' and request.POST.get('application'):
        applications = Application.select([Equal('slug', request.POST['application'])])
        if applications:
            application = applications[0]
            elements = ApplicationElement.select([Equal('application_id', application.id)])
            for element in elements:
                ApplicationElement.remove_object(element.id)
                if not ApplicationElement.exists(
                    [Equal('object_type', element.object_type), Equal('object_id', element.object_id)]
                ):
                    object_klass = get_publisher().get_object_class(element.object_type)
                    object_klass.remove_object(element.object_id)
            # remove application
            Application.remove_object(application.id)

    return JsonResponse({'err': 0})
