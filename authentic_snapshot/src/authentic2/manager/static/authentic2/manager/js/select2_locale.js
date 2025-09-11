(function ($) {
    $.fn.select2.defaults.set('language', {
         noResults: function () {
             return gettext("No matches found");
         },
         errorLoading: function (jqXHR, textStatus, errorThrown) {
             return gettext("Loading failed");
         },
         inputTooShort: function (args) {
             var n = args['minimum'] - args['input'].length;
             return interpolate(
                     ngettext("Please enter %s or more character", "Please enter %s or more characters", n),
                     [n]);
         },
         inputTooLong: function (args) {
             var n = args['input'].length - args['maximum'];
             return interpolate(
                     ngettext("Please delete %s character", "Please delete %s characters", n),
                     [n]);
         },
         maximumSelected: function (limit) {
             return interpolate(
                     ngettext("You can only select %s item", "You can only select %s items", limit),
                     [limit]);
         },
         loadingMore: function (pageNumber) {
             return gettext("Loading more results…");
         },
         searching: function () {
             return gettext("Searching…");
         }
    });
})(jQuery)
