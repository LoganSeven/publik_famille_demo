(function() {
    var initData = JSON.parse(document.getElementById('popup-response-constants').dataset.popupResponse);
    opener.dismissRelatedObjectPopup(window, initData.value, initData.obj, initData.edit_related_url, initData.view_related_url);
})();
