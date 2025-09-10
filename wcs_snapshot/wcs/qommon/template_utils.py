# w.c.s. - web application for online forms
# Copyright (C) 2005-2018  Entr'ouvert
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

# This is based on https://github.com/clokep/django-render-block,
# originally Django snippet 769, then Django snippet 942.
#
# Reduced to only support Django templates.


from django.template import Context, loader
from django.template.base import TextNode
from django.template.loader_tags import BLOCK_CONTEXT_KEY, BlockContext, BlockNode, ExtendsNode


class BlockNotFound(Exception):
    pass


def render_block_to_string(template_name, block_name, context=None):
    """
    Loads the given template_name and renders the given block with the given
    dictionary as context. Returns a string.

        template_name
            The name of the template to load and render. If it's a list of
            template names, Django uses select_template() instead of
            get_template() to find the template.
    """

    # Like render_to_string, template_name can be a string or a list/tuple.
    if isinstance(template_name, (tuple, list)):
        t = loader.select_template(template_name)
    else:
        t = loader.get_template(template_name)

    # Create a Django Context.
    context_instance = Context(context or {})

    # Get the underlying django.template.base.Template object.
    template = t.template

    # Bind the template to the context.
    with context_instance.bind_template(template):
        # Before trying to render the template, we need to traverse the tree of
        # parent templates and find all blocks in them.
        parent_template = _build_block_context(template, context_instance)

        try:
            return _render_template_block(template, block_name, context_instance)
        except BlockNotFound:
            # The block wasn't found in the current template.

            # If there's no parent template (i.e. no ExtendsNode), re-raise.
            if not parent_template:
                raise

            # Check the parent template for this block.
            return _render_template_block(parent_template, block_name, context_instance)


def _build_block_context(template, context):
    """Populate the block context with BlockNodes from parent templates."""

    # Ensure there's a BlockContext before rendering. This allows blocks in
    # ExtendsNodes to be found by sub-templates (allowing {{ block.super }} and
    # overriding sub-blocks to work).
    if BLOCK_CONTEXT_KEY not in context.render_context:
        context.render_context[BLOCK_CONTEXT_KEY] = BlockContext()
    block_context = context.render_context[BLOCK_CONTEXT_KEY]

    for node in template.nodelist:
        if isinstance(node, ExtendsNode):
            compiled_parent = node.get_parent(context)

            # Add the parent node's blocks to the context. (This ends up being
            # similar logic to ExtendsNode.render(), where we're adding the
            # parent's blocks to the context so a child can find them.)
            block_context.add_blocks(
                {n.name: n for n in compiled_parent.nodelist.get_nodes_by_type(BlockNode)}
            )

            _build_block_context(compiled_parent, context)
            return compiled_parent

        # The ExtendsNode has to be the first non-text node.
        if not isinstance(node, TextNode):
            break


def _render_template_block(template, block_name, context):
    """Renders a single block from a template."""
    return _render_template_block_nodelist(template.nodelist, block_name, context)


def _render_template_block_nodelist(nodelist, block_name, context):
    """Recursively iterate over a node to find the wanted block."""

    # Attempt to find the wanted block in the current template.
    for node in nodelist:
        # If the wanted block was found, return it.
        if isinstance(node, BlockNode):
            # No matter what, add this block to the rendering context.
            context.render_context[BLOCK_CONTEXT_KEY].push(node.name, node)

            # If the name matches, you're all set and we found the block!
            if node.name == block_name:
                return node.render(context)

        # If a node has children, recurse into them. Based on
        # django.template.base.Node.get_nodes_by_type.
        for attr in node.child_nodelists:
            try:
                new_nodelist = getattr(node, attr)
            except AttributeError:
                continue

            # Try to find the block recursively.
            try:
                return _render_template_block_nodelist(new_nodelist, block_name, context)
            except BlockNotFound:
                continue

    # The wanted block_name was not found.
    raise BlockNotFound("block with name '%s' does not exist" % block_name)
