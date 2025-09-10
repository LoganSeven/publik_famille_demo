function updateStatus()
{
    $('div.done').hide();
    $('span.afterjob').each(
        function () {
            var $afterjob_element = $(this);
            $afterjob_element.addClass('activity');
            $.getJSON('/afterjobs/' + $(this).attr('id'),
                function(data) {
                    $afterjob_element.text(data.message);
                    if (data.status == 'completed') {
                        if ($('div.done a[data-redirect-auto]').length) {
                            window.location = $('div.done a[data-redirect-auto]').attr('href');
                        }
                        $afterjob_element.addClass('activity-done');
                        $('.afterjob-running').hide();
                        $('.afterjob-done').show();
                        $('div.done').show();
                        $('#abort-job-button').hide();
                    } else if (data.status === 'failed' || data.status === 'aborted') {
                        $afterjob_element.addClass('activity-done');
                        $('#abort-job-button').hide();
                    } else {
                        window.setTimeout(updateStatus, 2500);
                    }
                }
            );
        }
    );
}

$(document).ready(updateStatus);
