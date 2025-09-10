# lingo - payment and billing system
# Copyright (C) 2022-2024  Entr'ouvert
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

import json
import tarfile

from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from rest_framework import permissions
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from lingo.agendas.chrono import refresh_agendas
from lingo.agendas.models import Agenda, CheckTypeGroup
from lingo.api.utils import APIErrorBadRequest
from lingo.export_import.models import Application, ApplicationElement
from lingo.invoicing.models import Campaign, Credit, Invoice, Payment, Regie
from lingo.invoicing.utils import import_site as invoicing_import_site
from lingo.pricing.models import CriteriaCategory, Pricing
from lingo.pricing.utils import import_site as pricing_import_site
from lingo.utils.wcs import WCSError

klasses = {
    klass.application_component_type: klass
    for klass in [Pricing, CriteriaCategory, Agenda, CheckTypeGroup, CriteriaCategory, Regie]
}
klasses['roles'] = Group
klasses_translation = {
    'lingo_agendas': 'agendas',  # agendas type is already used in chrono for Agenda
}
klasses_translation_reverse = {v: k for k, v in klasses_translation.items()}


def get_klass_from_component_type(component_type):
    try:
        return klasses[component_type]
    except KeyError:
        raise Http404


def import_site(components):
    # refresh agendas from chrono first
    refresh_agendas()
    # then import regies, payers
    invoicing_import_site(components)
    # and pricing components
    pricing_import_site(components)


class Index(GenericAPIView):
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request, *args, **kwargs):
        data = []
        for klass in klasses.values():
            if klass == Group:
                data.append(
                    {
                        'id': 'roles',
                        'text': _('Roles'),
                        'singular': _('Role'),
                        'urls': {
                            'list': request.build_absolute_uri(
                                reverse(
                                    'api-export-import-components-list',
                                    kwargs={'component_type': 'roles'},
                                )
                            ),
                        },
                        'minor': True,
                    }
                )
                continue
            component_type = {
                'id': klass.application_component_type,
                'text': klass.application_label_plural,
                'singular': klass.application_label_singular,
                'urls': {
                    'list': request.build_absolute_uri(
                        reverse(
                            'api-export-import-components-list',
                            kwargs={'component_type': klass.application_component_type},
                        )
                    ),
                },
            }
            if klass not in [Pricing]:
                component_type['minor'] = True
            data.append(component_type)

        return Response({'data': data})


index = Index.as_view()


def get_component_bundle_entry(request, component):
    if isinstance(component, Group):
        return {
            'id': component.role.slug if hasattr(component, 'role') else component.id,
            'text': component.name,
            'type': 'roles',
            'urls': {},
            # include uuid in object reference, this is not used for applification API but is useful
            # for authentic creating its role summary page.
            'uuid': component.role.uuid if hasattr(component, 'role') else None,
        }
    return {
        'id': str(component.slug),
        'text': component.label,
        'type': component.application_component_type,
        'urls': {
            'export': request.build_absolute_uri(
                reverse(
                    'api-export-import-component-export',
                    kwargs={
                        'component_type': component.application_component_type,
                        'slug': str(component.slug),
                    },
                )
            ),
            'dependencies': request.build_absolute_uri(
                reverse(
                    'api-export-import-component-dependencies',
                    kwargs={
                        'component_type': component.application_component_type,
                        'slug': str(component.slug),
                    },
                )
            ),
            'redirect': request.build_absolute_uri(
                reverse(
                    'api-export-import-component-redirect',
                    kwargs={
                        'component_type': component.application_component_type,
                        'slug': str(component.slug),
                    },
                )
            ),
        },
    }


class ListComponents(GenericAPIView):
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request, *args, **kwargs):
        klass = get_klass_from_component_type(kwargs['component_type'])
        order_by = 'slug'
        if klass == Group:
            order_by = 'name'
        response = [get_component_bundle_entry(request, x) for x in klass.objects.order_by(order_by)]
        return Response({'data': response})


list_components = ListComponents.as_view()


class ExportComponent(GenericAPIView):
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request, slug, *args, **kwargs):
        klass = get_klass_from_component_type(kwargs['component_type'])
        serialisation = get_object_or_404(klass, slug=slug).export_json()
        return Response({'data': serialisation})


export_component = ExportComponent.as_view()


class ComponentDependencies(GenericAPIView):
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request, slug, *args, **kwargs):
        klass = get_klass_from_component_type(kwargs['component_type'])
        component = get_object_or_404(klass, slug=slug)

        def dependency_dict(element):
            if isinstance(element, dict):
                return element
            return get_component_bundle_entry(request, element)

        try:
            dependencies = [dependency_dict(x) for x in component.get_dependencies() if x]
        except WCSError as e:
            return Response({'err': 1, 'err_desc': str(e)}, status=400)
        return Response({'err': 0, 'data': dependencies})


component_dependencies = ComponentDependencies.as_view()


def component_redirect(request, component_type, slug):
    klass = get_klass_from_component_type(component_type)
    component = get_object_or_404(klass, slug=slug)
    if klass == Pricing:
        return redirect(reverse('lingo-manager-pricing-detail', kwargs={'pk': component.pk}))
    if klass == CriteriaCategory:
        return redirect(reverse('lingo-manager-pricing-criteria-list'))
    if klass == Agenda:
        return redirect(reverse('lingo-manager-agenda-detail', kwargs={'pk': component.pk}))
    if klass == CheckTypeGroup:
        return redirect(reverse('lingo-manager-check-type-list'))
    if klass == Regie:
        return redirect(reverse('lingo-manager-invoicing-regie-detail', kwargs={'pk': component.pk}))
    raise Http404


class BundleCheck(GenericAPIView):
    permission_classes = (permissions.IsAdminUser,)

    def post(self, request, *args, **kwargs):
        return Response({'err': 0, 'data': {}})


bundle_check = BundleCheck.as_view()


class BundleImport(GenericAPIView):
    permission_classes = (permissions.IsAdminUser,)
    install = True

    def post(self, request, *args, **kwargs):
        bundle = request.FILES['bundle']
        components = {}
        try:
            with tarfile.open(fileobj=bundle) as tar:
                try:
                    manifest = json.loads(tar.extractfile('manifest.json').read().decode())
                except KeyError:
                    raise APIErrorBadRequest(_('Invalid tar file, missing manifest'))
                self.application = Application.update_or_create_from_manifest(
                    manifest,
                    tar,
                    editable=not self.install,
                )

                for element in manifest.get('elements'):
                    component_type = element['type']
                    if component_type not in klasses or element['type'] == 'roles':
                        continue
                    component_type = klasses_translation.get(component_type, component_type)
                    if component_type not in components:
                        components[component_type] = []
                    try:
                        component_content = (
                            tar.extractfile('%s/%s' % (element['type'], element['slug'])).read().decode()
                        )
                    except KeyError:
                        raise APIErrorBadRequest(
                            _('Invalid tar file, missing component %(type)s/%(slug)s') % element
                        )
                    components[component_type].append(json.loads(component_content).get('data'))
        except tarfile.TarError:
            raise APIErrorBadRequest(_('Invalid tar file'))

        # init cache of application elements, from manifest
        self.application_elements = set()
        # import agendas
        self.do_something(components)
        # create application elements
        self.link_objects(components)
        # remove obsolete application elements
        self.unlink_obsolete_objects()
        return Response({'err': 0})

    def do_something(self, components):
        if components:
            import_site(components)

    def link_objects(self, components):
        for component_type, component_list in components.items():
            component_type = klasses_translation_reverse.get(component_type, component_type)
            klass = klasses[component_type]
            for component in component_list:
                try:
                    existing_component = klass.objects.get(slug=component['slug'])
                except klass.DoesNotExist:
                    pass
                else:
                    element = ApplicationElement.update_or_create_for_object(
                        self.application, existing_component
                    )
                    self.application_elements.add(element.content_object)
                    if self.install is True:
                        existing_component.take_snapshot(
                            comment=_('Application (%s)') % self.application,
                            application=self.application,
                        )

    def unlink_obsolete_objects(self):
        known_elements = ApplicationElement.objects.filter(application=self.application)
        for element in known_elements:
            if element.content_object not in self.application_elements:
                element.delete()


bundle_import = BundleImport.as_view()


class BundleDeclare(BundleImport):
    install = False

    def do_something(self, components):
        # no installation on declare
        pass


bundle_declare = BundleDeclare.as_view()


class BundleUnlink(GenericAPIView):
    permission_classes = (permissions.IsAdminUser,)

    def post(self, request, *args, **kwargs):
        if request.POST.get('application'):
            try:
                application = Application.objects.get(slug=request.POST['application'])
            except Application.DoesNotExist:
                pass
            else:
                application.delete()

        return Response({'err': 0})


bundle_unlink = BundleUnlink.as_view()


class BundleUninstall(GenericAPIView):
    permission_classes = (permissions.IsAdminUser,)

    def post(self, request, *args, **kwargs):
        if request.POST.get('application'):
            try:
                application = Application.objects.get(slug=request.POST['application'])
            except Application.DoesNotExist:
                pass
            else:
                for element in ApplicationElement.objects.filter(application=application):
                    if element.content_object is None or len(element.content_object.applications) > 1:
                        # already removed, or also used in another app, ignore
                        continue
                    element.content_object.delete()
                application.delete()
        return Response({'err': 0})


bundle_uninstall = BundleUninstall.as_view()


class ForcedRollback(Exception):
    pass


class BundleUninstallCheck(GenericAPIView):
    permission_classes = (permissions.IsAdminUser,)

    def post(self, request, *args, **kwargs):
        if request.POST.get('application'):
            try:
                application = Application.objects.get(slug=request.POST['application'])
            except Application.DoesNotExist:
                pass
            else:
                try:
                    # 1st pass with common objects
                    content_type = ContentType.objects.get_for_model(Regie)
                    for element in ApplicationElement.objects.filter(
                        application__slug=request.POST['application'], content_type=content_type
                    ):
                        regie = element.content_object
                        if regie is None or len(regie.applications) > 1:
                            # already removed, or also used in another app, ignore
                            continue
                        for model in (Invoice, Credit, Campaign, Payment):
                            if model.objects.filter(regie=regie).exists():
                                return Response(
                                    {
                                        'err': 1,
                                        'err_desc': _('Regie (%(slug)s) referenced in "%(objects)s"')
                                        % {'slug': regie.slug, 'objects': model._meta.verbose_name_plural},
                                    }
                                )
                    # 2nd pass to make sure all objects with on_delete=models.PROTECT are covered
                    with transaction.atomic():
                        for element in ApplicationElement.objects.filter(application=application):
                            if element.content_object is None or len(element.content_object.applications) > 1:
                                # already removed, or also used in another app, ignore
                                continue
                            element.content_object.delete()
                        connection.check_constraints()
                        raise ForcedRollback()
                except IntegrityError:
                    return Response({'err': 1, 'err_desc': _('Existing data')})
                except ForcedRollback:
                    # all good
                    pass

        return Response({'err': 0})


bundle_uninstall_check = BundleUninstallCheck.as_view()
