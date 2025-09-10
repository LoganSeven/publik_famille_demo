$(document).ready(
    function () {
        if ($('ul.biglist.sortable').length) {
            /* work around a jquery bug with .sortable() called on a container
             * set with position: relative and overflow properties */
            $('#main-content').css('overflow', 'inherit');
        }
        $('ul.biglist.sortable li').each(function(i, elem) {
            if ($(elem).is('.page-in-multipage')) {
                $('<span class="no-handle">⣿</span>').prependTo(elem);
            } else {
                $('<span class="handle">⣿</span>').prependTo(elem);
            }
        });

        const $move_page_field = $('<div class="move-page-field-content"></div>');
        const $move_page_field_link = $('<a class="move-page-field-link"></a>').appendTo($move_page_field);
        $move_page_field.dialog({
            autoOpen: false,
            minHeight: 0,
            dialogClass: "move-page-field oneline-dialog feedback-on-open",
            draggable: false,
            open: function() {
                $move_page_field.dialog('widget').removeClass('feedback-on-open');
            },
            close: function() {
                $move_page_field.dialog('widget').addClass('feedback-on-open');
            }
        });

        $('ul.biglist.sortable:not(.readonly)').sortable(
            {
                handle: '.handle',
                items: '.biglistitem:not(.page-in-multipage)',
                scroll: true,
                helper: 'clone',
                helperclass: 'sortHelper',
                activeclass :     'sortableactive',
                hoverclass :     'sortablehover',
                tolerance: 'pointer',
                update : function(event, ui)
                {
                    var page_no_label = $(this).data('page-no-label');
                    var page_index = 1;
                    result = '';
                    items = $(ui.item).parent().find('li');
                    for (i=0; i < items.length; i++) {
                        var item = items[i];
                        var item_id = item.dataset.id;
                        if (item_id !== undefined) {
                          result += item_id + ';';
                        }
                        if ($(item).find('span.page-no').length) {
                          $(item).find('span.page-no').text(page_no_label.replace('***', page_index));
                          page_index += 1;
                        }
                    }
                    $move_page_field.dialog('close');
                    var order_function = $(this).data('order-function') || 'update_order';
                    $.post(order_function, {'order': result, 'element': $(ui.item)[0].dataset.id})
                    .done(function(data) {
                        if (data['success'] != "ok") return;
                        if (!data['additional-action']) return;
                        $move_page_field_link.attr('href', data['additional-action']['url']).html(data['additional-action']['message']);
                        $move_page_field.dialog('option', 'position', { my: 'left top', at: 'left bottom', of: $(ui.item) });
                        $move_page_field.dialog('open');
                    });
                },
            }
        );
    }
);

