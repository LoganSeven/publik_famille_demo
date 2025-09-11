$(function() {
    $('select.address-autocomplete').select2({
        ajax: {
            delay: 250,
            dataType: 'json',
            data: function(params) {
                return {q: params.term, page_limit: 10};
            },
            processResults: function (data, params) {
                return {results: data.data};
            },
            url: function (params) {
                return $(this).data('select2-url')
            }
        }
    }).on('select2:select', function(e) {
        var data = e.params.data;
        if (data) {
            var address = undefined;
            if (typeof data.address == "object") {
                address = data.address;
            } else {
                address = data;
            }
            var road = address.road || address.nom_rue;
            var house_number = address.house_number || address.numero;
            var city = address.city || address.nom_commune;
            var postcode = address.postcode || address.code_postal;
            var number_and_street = null;
            if (house_number && road) {
                number_and_street = house_number + ' ' + road;
            } else {
                number_and_street = road;
            }
            if ($('#id_house_number').length) {
                $('#id_house_number').val(house_number);
                $('#id_address').val(road);
            } else {
                $('#id_address').val(number_and_street);
            }
            $('#id_city').val(city);
            $('#id_zipcode').val(postcode);
        }
    });
    $('#id_house_number, #id_address, #id_city, #id_zipcode').attr('readonly', 'readonly');
    $('#manual-address').on('change', function() {
        $('#id_house_number, #id_address, #id_city, #id_zipcode').attr('readonly', this.checked ? null : 'readonly');
    });
    if ($('#id_house_number').val() || $('#id_address').val() || $('#id_city').val() || $('#id_zipcode').val()) {
        var data = {
            id: 1,
            text: ''
        }
        $.each(['#id_house_number', '#id_address', '#id_zipcode', '#id_city'], function(idx, value) {
            if ($(value).val()) {
                if (data.text) {
                    data.text += ' ';
                }
                data.text += $(value).val();
            }
        })
        var newOption = new Option(data.text, data.id, false, false);
        $('select.address-autocomplete').append(newOption).trigger('change');
    }
});
