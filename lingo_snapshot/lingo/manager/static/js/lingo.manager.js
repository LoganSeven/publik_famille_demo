$(function() {
  function prepare_dynamic_fields() {
    $('[data-dynamic-display-parent]').off('change input').on('change input', function() {
      var sel1 = '[data-dynamic-display-child-of="' + $(this).attr('name') + '"]';
      var sel2 = '[data-dynamic-display-value="' + $(this).val() + '"]';
      var sel3 = '[data-dynamic-display-value-in*=" ' + $(this).val() + ' "]';
      $(sel1).addClass('field-hidden').parents('.widget').hide();
      $(sel1 + sel2).removeClass('field-hidden').parents('.widget').show();
      $(sel1 + sel3).removeClass('field-hidden').parents('.widget').show();
      $(sel1).trigger('change');
    });
    $('[data-dynamic-display-child-of]').addClass('field-hidden').parents('.widget').hide();
    $('select[data-dynamic-display-parent]').trigger('change');
    $('[data-dynamic-display-parent]:checked').trigger('change');
  }
  prepare_dynamic_fields();
  $(document).on('gadjo:dialog-loaded', prepare_dynamic_fields);

  $(document).on('click', '#add-pricing-variable-form', function() {
    if (typeof property_forms === "undefined") {var property_forms = $('.pricing-variable-form');}
    if (typeof total_forms === "undefined") {var total_form = $('#id_form-TOTAL_FORMS');}
    if (typeof form_num === "undefined") {var form_num = property_forms.length - 1;}
    var new_form = $(property_forms[0]).clone();
    var form_regex = RegExp(`form-(\\d){1}-`,'g');
    form_num++;
    new_form.html(new_form.html().replace(form_regex, `form-${form_num}-`));
    new_form.appendTo('#pricing-variable-forms tbody');
    $('#id_form-' + form_num + '-key').val('');
    $('#id_form-' + form_num + '-value').val('');
    total_form.val(form_num + 1);
  })

  $('.sortable').sortable({
    handle: '.handle',
    items: '.sortable-item',
    update : function(event, ui) {
      var new_order = '';
      $(this).find('.sortable-item').each(function(i, x) {
        var item_id = $(x).data('item-id');
        if (new_order) {
          new_order += ',';
        }
        new_order += item_id;
      });
      $.ajax({
        url: $(this).data('order-url'),
        data: {'new-order': new_order}
      });
    }
  });

  $(document).on('click', '.invoicing-element-list .togglable', function(event) {
    event.preventDefault();
    var $toggle = $(this);
    var $tr = $toggle.parents('tr');
    var invoicing_element_id = $tr.data('invoicing-element-id');
    if ($('tr[data-related-invoicing-element-id="' + invoicing_element_id + '"]').length == 0) {
      $.ajax({
        url: $tr.data('invoicing-element-lines-url')
      }).done(function(html) {
        $tr.toggleClass('toggled').toggleClass('untoggled');
        $(html).insertAfter($tr);
      });
    } else {
      $tr.toggleClass('toggled').toggleClass('untoggled');
      $('tr[data-related-invoicing-element-id="' + invoicing_element_id + '"]').toggle();
    }
  });

  $(document).on('click', '.lines .togglable', function(event) {
    event.preventDefault();
    var $toggle = $(this);
    var $tr = $toggle.parents('tr');
    var line_id = $tr.data('line-id');
    var $details = $('tr[data-details-for-line-id=' + line_id + ']');
    $tr.toggleClass('toggled').toggleClass('untoggled');
    $details.toggle();
  });

  /* focus tab from #open:<tab slug> anchor, to point to open panel */
  if (document.location.hash && document.location.hash.indexOf('#open:') == 0) {
    const $tab_button = $('#tab-' + document.location.hash.substring(6) + '[role=tab]');
    if ($tab_button.length) {
      $('.pk-tabs')[0].tabs.selectTab($tab_button[0]);
    }
  }
});
