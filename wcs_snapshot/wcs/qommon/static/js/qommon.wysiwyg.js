/* makes sure javascript loading is not interrupted by ckeditor jquery adapter
 * throwing an error about the environment being incompatible. */
CKEDITOR.env.origIsCompatible = CKEDITOR.env.isCompatible;
CKEDITOR.env.isCompatible = true;

/* do not turn all contenteditable into ckeditor, as some pages may have both
 * godo and ckeditor */
CKEDITOR.disableAutoInline = true;

$(document).ready( function() {
  if (CKEDITOR.env.origIsCompatible == false) {
    /* bail out if ckeditor advertised itself as not supported */
    return;
  }
  if (typeof($().ckeditor) === 'undefined') {
    /* the jquery adapter couldn't be loaded, don't bother failing further down */
    return;
  }
  $('div.WysiwygTextWidget textarea').each(function(idx, textarea) {
    var config = $(textarea).data('config');
    $(textarea).ckeditor(config);
  });
  $(document).on('gadjo:dialog-loaded', function(e, dialog) {
    var $textarea = $(dialog).find('div.WysiwygTextWidget textarea');
    if ($textarea.length == 0) return;
    var config = $textarea.data('config');
    config.width = $(dialog).width();
    config.height = $textarea.height() + 100;
    $textarea.ckeditor(config);
  });
});
