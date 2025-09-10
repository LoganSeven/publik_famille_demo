# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
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

import re

from quixote import get_publisher
from quixote.html import htmltext

from .. import _


def _find_vc_version():
    '''Find current version of the source code'''
    import os.path
    import subprocess

    base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    package = None
    if os.path.exists(os.path.join(base, 'qommon')):
        package = os.path.basename(base)
    if os.path.exists(os.path.join(base, '..', 'setup.py')):
        srcdir = os.path.join(base, '..')
    else:
        srcdir = None

    # not run from source directory
    if not srcdir:
        # but have a qommon container
        if not package:
            return None
        if os.path.exists('/etc/debian_version'):
            # debian case
            try:
                with subprocess.Popen(
                    ['dpkg', '-l', package], stdout=subprocess.PIPE, stderr=subprocess.STDOUT
                ) as process:
                    version = process.communicate()[0].splitlines()[-1].split()[2]
                    if process.returncode == 0:
                        return '%s %s (Debian)' % (package, version.decode())
            except Exception:
                pass
        return None

    revision = None
    try:
        with open(os.path.join(srcdir, 'setup.py')) as fd:
            setup_content = fd.read()
        version_line = [x for x in setup_content.splitlines() if 'version' in x][0]
        version = re.split('"|\'', version_line.split()[2])[1]
    except Exception:
        version = None

    if os.path.exists(os.path.join(srcdir, '.git')):
        try:
            with subprocess.Popen(
                ['git', 'log', '--pretty=oneline', '-1'], stdout=subprocess.PIPE, cwd=srcdir
            ) as process:
                output = process.communicate()[0]
            rev = str(output.split()[0].decode('ascii'))
            with subprocess.Popen(['git', 'branch'], stdout=subprocess.PIPE, cwd=srcdir) as process:
                output = process.communicate()[0]
            starred_line = [x for x in output.splitlines() if x.startswith(b'*')][0]
            branch = str(starred_line.split()[1].decode('ascii'))
            url = 'https://repos.entrouvert.org/%s.git/commit/?id=%s' % (package, rev)
            if version:
                revision = htmltext('%s %s <a href="%s">git %s\'s branch rev:%s</a>') % (
                    package,
                    version,
                    url,
                    branch,
                    rev[:8],
                )
            else:
                revision = htmltext('%s <a href="%s">git %s\'s branch rev:%s</a>') % (
                    package,
                    url,
                    branch,
                    rev[:8],
                )
        except OSError:
            pass
    else:
        if version:
            revision = '%s %s (Tarball)' % (package, version)
        else:
            revision = '%s (Tarball)' % (package)

    if not revision:
        return None
    return revision


vc_version = _find_vc_version()


def get_vc_version():
    return vc_version


def command_icon(url, type, label=None, popup=False):
    labels = {
        'edit': _('Edit'),
        'remove': _('Remove'),
        'duplicate': _('Duplicate'),
        'view': _('View'),
    }
    if label:
        klass = 'button'
    else:
        klass = ''
        label = labels[type]
    root_url = get_publisher().get_application_static_files_root_url()
    rel = ''
    if popup:
        rel = 'popup'
    return (
        htmltext(
            '''<span class="%(type)s">
  <a href="%(url)s" class="%(klass)s" rel="%(rel)s" title="%(label)s">%(label)s</a>
</span>'''
        )
        % locals()
    )
