from unittest import mock

from django.core.management import call_command


def test_makemessages():
    with mock.patch('django.core.management.commands.makemessages.Command.handle') as handle:
        handle.return_value = ''
        call_command('makemessages')
        assert handle.call_args[1].get('add_location') == 'file'
        assert handle.call_args[1].get('no_obsolete') is True
