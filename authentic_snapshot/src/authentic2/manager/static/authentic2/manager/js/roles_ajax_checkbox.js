(function ($, window, undefined) {
	function fix_underterminate() {
		$('.indeterminate').each(function (i, elem) {
			 elem.indeterminate = true;
		})
	}
	$(document).on('gadjo:content-update', function () {
		fix_underterminate();
	});
	$(function () {
		$('body').on('click', 'input.role-member', function (e) {
			e.stopPropagation();
		});
		$('body').on('change', 'input.role-member', function (e) {
			var $target = $(e.target);
			var pk = e.target.name.split('-')[1];
			data = {
				'csrfmiddlewaretoken': window.csrf_token,
				'role': pk,
				'action': ($target.is(':checked') && 'add') || 'remove',
			};
			console.log(data);
			var $overlay = $('<div class="waiting"/>');
			$('body')[0].appendChild($overlay[0]);
			$.post(window.location.href, data).done(function () {
				window.update_content(window.location.href);
				$('body')[0].removeChild($overlay[0]);

			});
		});
	})
})(jQuery, window, undefined);
