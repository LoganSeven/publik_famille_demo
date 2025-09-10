function prepare_widget_list_elements() {
        $('[name$=add_element]').click(
            function() {
                /* get prefix from clicked element */
                var name_attr = $(this).attr('name');
                var prefix = name_attr.substr(0, name_attr.length-12); // 12 == len($add_element)

                /* get last row node */
                var row = $(this).parents('div').parents('div').prev()[0];
                if (row.tagName == 'BR') {
                    row = $(row).prev()[0];
                }
                if (row.tagName == 'TABLE') {
                    row = $(row).find('tr:last');
                }

                if ($(row).is('.BlockSubWidget')) {
                    /* don't apply on block widgets as they need to handle
                     * maximum number items and the "repeated data" bug is too
                     * annoying.
                     */
                    return true;
                }
                if ($(row).find('[name$=add_element], [data-dynamic-display-value]').length > 0) {
                    /* this has complex widgets, don't use
                     * javascript in that case */
                    return true;
                }

                /* clone the row */
                var new_row = $(row).clone();

                /* fix up form element name */
                $(new_row).find('[name^=' + prefix + ']').each(
                    function() {
                        var $element = $(this);
                        if ($element.attr('type') == 'text') {
                            $element.attr('value', '');
                        }
                        var cur_name = $element.attr('name');
                        var pos = cur_name.indexOf('element', prefix.length) + 7; // 7 == len(element)
                        var index = cur_name.substring(pos, cur_name.length);
                        var element_regex = RegExp(`\\$element(\\d+)`, 'g');
                        index = parseInt(index) + 1;
                        $(new_row).html($(new_row).html().replace(element_regex, `$element${index}`));
                        if ($(new_row).attr('data-widget-name')) {
                            $(new_row).attr('data-widget-name', $(new_row).attr('data-widget-name').replace(element_regex, `$element${index}`));
                        }
                    }
                );
                if (name_attr == 'to$add_element' || name_attr == 'roles$add_element') {
                  /* replace 1st list element by an empty label, as it's used
                   * to remove an actual selection */
                  $(new_row).find('option[value=""]').first().text('----');
                }

                /* add new row after the last row */
                $(row).after($(new_row));

                /* increase added_element counter */
                var $added_element_input = $(`input[name="${prefix}$added_elements"]`)
                $added_element_input.val(parseInt($added_element_input.val()) + 1)

                /* trigger advanced widget setup */
                $(document).trigger('wcs:new-widgets-on-page');

                return false;
            }
        );

        $('.widget.sortable input').each(function () {
            $('<span class="handle">⣿</span>').insertBefore($(this));
        });
        $('.widget.sortable .content').sortable({
            handle: '.handle',
            start: function(event, ui) {
                $('.widget.StringWidget input', $(this)).each(function () {
                    $(this).attr('value', $(this).val());  // save potential new values before the move
                });
            },
            update : function(event, ui) {
                $('.widget.StringWidget', $(this)).each(function (index) {
                    var element_regex = RegExp(`\\$element(\\d+)`, 'g');
                    $(this).html($(this).html().replace(element_regex, `$element${index}`));
                    $(this).attr('data-widget-name', $(this).attr('data-widget-name').replace(element_regex, `$element${index}`));
                });
            },
        });
    }

$(document).ready(prepare_widget_list_elements);
