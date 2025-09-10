# This file is sourced by "execfile" from lingo.settings

import os

PROJECT_NAME = 'lingo'

#
# hobotization (multitenant)
#
exec(open('/usr/lib/hobo/debian_config_common.py').read())

#
# local settings
#
exec(open(os.path.join(ETC_DIR, 'settings.py')).read())

# run additional settings snippets
exec(open('/usr/lib/hobo/debian_config_settings_d.py').read())
