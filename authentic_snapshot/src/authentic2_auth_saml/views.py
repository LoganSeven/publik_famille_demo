from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.generic import DetailView
from mellon.utils import get_idp

from authentic2.utils.misc import redirect_to_login

from .models import SAMLAuthenticator


def login(request, authenticator, *args, **kwargs):
    context = kwargs.pop('context', {}).copy()
    submit_name = 'login-saml-%s' % authenticator.slug
    context['submit_name'] = submit_name
    context['authenticator'] = authenticator
    if request.method == 'POST' and submit_name in request.POST:
        from .adapters import AuthenticAdapter

        settings = authenticator.settings
        AuthenticAdapter().load_idp(settings, authenticator.order)
        return redirect_to_login(
            request, login_url='mellon_login', params={'entityID': settings['ENTITY_ID']}
        )
    return render(
        request,
        ['authentic2_auth_saml/login_%s.html' % authenticator.slug, 'authentic2_auth_saml/login.html'],
        context,
    )


def profile(request, *args, **kwargs):
    context = kwargs.pop('context', {})
    user_saml_identifiers = request.user.saml_identifiers.all()
    if not user_saml_identifiers:
        return ''
    for user_saml_identifier in user_saml_identifiers:
        user_saml_identifier.idp = get_idp(user_saml_identifier.issuer.entity_id)
    context['user_saml_identifiers'] = user_saml_identifiers
    return render_to_string('authentic2_auth_saml/profile.html', context, request=request)


class SAMLAuthenticatorMetadataView(DetailView):
    model = SAMLAuthenticator

    def get(self, *args, **kwargs):
        authenticator = self.get_object()
        if not authenticator.metadata:
            raise Http404()

        return HttpResponse(authenticator.metadata, content_type='text/xml')


authenticator_metadata = SAMLAuthenticatorMetadataView.as_view()
