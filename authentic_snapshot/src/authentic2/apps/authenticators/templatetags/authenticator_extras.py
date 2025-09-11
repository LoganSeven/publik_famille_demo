from django import template

from authentic2.utils.misc import make_url

register = template.Library()


@register.simple_tag(takes_context=True)
def absolute_url(context, view_name, *args, **kwargs):
    return make_url(view_name, request=context['request'], absolute=True)
