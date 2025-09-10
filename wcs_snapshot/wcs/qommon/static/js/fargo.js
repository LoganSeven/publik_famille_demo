
$(function() {
  var iframe = $('<iframe frameborder="0" marginwidth="0" marginheight="0" allowfullscreen></iframe>');
  var dialog = $("<div></div>").append(iframe).appendTo("body").dialog({
        closeText: WCS_I18N.close,
        autoOpen: false,
        modal: true,
        resizable: false,
        width: "auto",
        height: "auto",
        close: function () {
            iframe.attr("src", "");
        }
  });
  $('p.use-file-from-fargo span').click(function(e) {
    e.preventDefault();
    var base_widget = $(this).parents('.file-upload-widget');
    document.fargo_set_token = function (token, title) {
       if (token) {
         $(base_widget).find('.fileprogress').removeClass('upload-error');
         $(base_widget).find('.fileprogress .bar').text('');
         $(base_widget).find('.filename').text(title);
         $(base_widget).find('.fileinfo').show();
         $(base_widget).find('input[type=text]').val(token);
         $(base_widget).find('input[type=file]').hide();
         $(base_widget).find('.use-file-from-fargo').hide();
         $(base_widget).find('input[type=file]').trigger('change');
         $(base_widget).addClass('has-file').removeClass('has-no-file');
       }
       document.fargo_close_dialog();
    }
    document.fargo_close_dialog = function () {
       document.fargo_set_token = undefined;
       dialog.dialog('close');
    }
    var src = $(this).data('src');
    var title = $(this).data("title");
    var width = $(this).data("width");
    var height = $(this).data("height");
    iframe.attr({
        width: parseInt(width),
        height: parseInt(height),
        src: src
    });
    dialog.dialog("option", "title", title);
    dialog.dialog("open");
  });
});
