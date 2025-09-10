
$(document).ready(
    function () {
        $('div.or-existing input').change(
            function() {
                var field = $(this).parents('div').parents('div').prev().prev().find('input')[0];
                $(field).attr('disabled', this.checked ? 'disabled' : '');
           }
        );
    }
);
