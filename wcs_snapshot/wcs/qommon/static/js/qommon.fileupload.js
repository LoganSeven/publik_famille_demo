var ExifRestorer = (function()
{
    // from http://www.perry.cz/files/ExifRestorer.js
    // based on MinifyJpeg
    // http://elicon.blog57.fc2.com/blog-entry-206.html

    var ExifRestorer = {};

    ExifRestorer.KEY_STR = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=";

    ExifRestorer.encode64 = function(input)
    {
        var output = "",
            chr1, chr2, chr3 = "",
            enc1, enc2, enc3, enc4 = "",
            i = 0;

        do {
            chr1 = input[i++];
            chr2 = input[i++];
            chr3 = input[i++];

            enc1 = chr1 >> 2;
            enc2 = ((chr1 & 3) << 4) | (chr2 >> 4);
            enc3 = ((chr2 & 15) << 2) | (chr3 >> 6);
            enc4 = chr3 & 63;

            if (isNaN(chr2)) {
               enc3 = enc4 = 64;
            } else if (isNaN(chr3)) {
               enc4 = 64;
            }

            output = output +
               this.KEY_STR.charAt(enc1) +
               this.KEY_STR.charAt(enc2) +
               this.KEY_STR.charAt(enc3) +
               this.KEY_STR.charAt(enc4);
            chr1 = chr2 = chr3 = "";
            enc1 = enc2 = enc3 = enc4 = "";
        } while (i < input.length);

        return output;
    };

    ExifRestorer.restore = function(origFileBase64, resizedFileBase64)
    {
        if (!origFileBase64.match("data:image/jpeg;base64,"))
        {
            return resizedFileBase64;
        }

        var rawImage = this.decode64(origFileBase64.replace("data:image/jpeg;base64,", ""));
        var segments = this.slice2Segments(rawImage);

        var image = this.exifManipulation(resizedFileBase64, segments);

        return this.encode64(image);
    };

    ExifRestorer.restore_as_blob = function(origFileBase64, resizedFileBase64) {
        var b64Data = ExifRestorer.restore(origFileBase64, resizedFileBase64);
        contentType = 'image/jpeg';
        sliceSize = 512;

        var byteCharacters = atob(b64Data);
        var byteArrays = [];

        for (var offset = 0; offset < byteCharacters.length; offset += sliceSize) {
            var slice = byteCharacters.slice(offset, offset + sliceSize);
            var byteNumbers = new Array(slice.length);
            for (var i = 0; i < slice.length; i++) {
                byteNumbers[i] = slice.charCodeAt(i);
            }
            var byteArray = new Uint8Array(byteNumbers);
            byteArrays.push(byteArray);
        }
        return new Blob(byteArrays, {type: contentType});
    };

    ExifRestorer.exifManipulation = function(resizedFileBase64, segments)
    {
        var exifArray = this.getExifArray(segments),
            newImageArray = this.insertExif(resizedFileBase64, exifArray),
            aBuffer = new Uint8Array(newImageArray);

        return aBuffer;
    };

    ExifRestorer.getExifArray = function(segments)
    {
        var seg;
        for (var x = 0; x < segments.length; x++)
        {
            seg = segments[x];
            if (seg[0] == 255 & seg[1] == 225) //(ff e1)
            {
                return seg;
            }
        }
        return [];
    };

    ExifRestorer.insertExif = function(resizedFileBase64, exifArray)
    {
        var imageData = resizedFileBase64.replace("data:image/jpeg;base64,", ""),
            buf = this.decode64(imageData),
            separatePoint = buf.indexOf(255,3),
            mae = buf.slice(0, separatePoint),
            ato = buf.slice(separatePoint),
            array = mae;

        array = array.concat(exifArray);
        array = array.concat(ato);
        return array;
    };


    ExifRestorer.slice2Segments = function(rawImageArray)
    {
        var head = 0,
            segments = [];

        while (1)
        {
            if (rawImageArray[head] == 255 & rawImageArray[head + 1] == 218){break;}
            if (rawImageArray[head] == 255 & rawImageArray[head + 1] == 216)
            {
                head += 2;
            }
            else
            {
                var length = rawImageArray[head + 2] * 256 + rawImageArray[head + 3],
                    endPoint = head + length + 2,
                    seg = rawImageArray.slice(head, endPoint);
                segments.push(seg);
                head = endPoint;
            }
            if (head > rawImageArray.length){break;}
        }

        return segments;
    };

    ExifRestorer.decode64 = function(input)
    {
        var output = "",
            chr1, chr2, chr3 = "",
            enc1, enc2, enc3, enc4 = "",
            i = 0,
            buf = [];

        // remove all characters that are not A-Z, a-z, 0-9, +, /, or =
        var base64test = /[^A-Za-z0-9\+\/\=]/g;
        if (base64test.exec(input)) {
            alert("There were invalid base64 characters in the input text.\n" +
                  "Valid base64 characters are A-Z, a-z, 0-9, '+', '/',and '='\n" +
                  "Expect errors in decoding.");
        }
        input = input.replace(/[^A-Za-z0-9\+\/\=]/g, "");

        do {
            enc1 = this.KEY_STR.indexOf(input.charAt(i++));
            enc2 = this.KEY_STR.indexOf(input.charAt(i++));
            enc3 = this.KEY_STR.indexOf(input.charAt(i++));
            enc4 = this.KEY_STR.indexOf(input.charAt(i++));

            chr1 = (enc1 << 2) | (enc2 >> 4);
            chr2 = ((enc2 & 15) << 4) | (enc3 >> 2);
            chr3 = ((enc3 & 3) << 6) | enc4;

            buf.push(chr1);

            if (enc3 != 64) {
               buf.push(chr2);
            }
            if (enc4 != 64) {
               buf.push(chr3);
            }

            chr1 = chr2 = chr3 = "";
            enc1 = enc2 = enc3 = enc4 = "";

        } while (i < input.length);

        return buf;
    };

    return ExifRestorer;
})();

$.WcsFileUpload = {
    prepare: function() {
        var base_widget = $(this);
        var image_resize = $(this).find('.file-button').data('image-resize');
        if (typeof(FileReader) === "undefined") {
            image_resize = false;
        }
        if ($(base_widget).find('input[type=text]').val()) {
            $(base_widget).find('input[type=file]').hide();
            $(base_widget).find('.use-file-from-fargo').hide();
            $(base_widget).addClass('has-file');
        } else {
            $(base_widget).find('.fileinfo').hide();
            $(base_widget).addClass('has-no-file');
        }
        $(this).find('input[type=file]').fileupload({
            dropZone: base_widget,
            pasteZone: base_widget,
            dataType: 'json',
            add: function (e, data) {
                var accepted_mimetypes = $(this).attr('accept');
                if (accepted_mimetypes) {
                    accepted_mimetypes = accepted_mimetypes.split(',');
                    var file_mimetype = data.files[0].type;
                    var valid_mimetype = false;
                    for (var i in accepted_mimetypes) {
                        var mime_type = accepted_mimetypes[i];
                        if (mime_type.substring(mime_type.length-2, mime_type.length) == '/*') {
                            if (file_mimetype.substring(0, mime_type.length-1) == mime_type.substring(0, mime_type.length-1)) {
                                valid_mimetype = true;
                                break;
                            }
                        } else {
                            if (file_mimetype == mime_type) {
                                valid_mimetype = true;
                                break;
                            }
                        }
                    }
                    if (!valid_mimetype) {
                        $(base_widget).find('.fileprogress').addClass('upload-error');
                        $(base_widget).find('.fileprogress .bar').text(WCS_I18N.file_type_error);
                        $(base_widget).find('.fileprogress .bar').attr('aria-label', WCS_I18N.file_type_error);
                        $(base_widget).find('.fileprogress').show().focus();
                        return;
                    }
                }

                if (image_resize && (
                        data.files[0].type == 'image/jpeg' ||
                        data.files[0].type == 'image/png')) {

                    $(base_widget).find('.fileprogress')[0].style.setProperty('--upload-progression', '0%');
                    $(base_widget).find('.fileprogress .bar').text(
                        $(base_widget).find('.fileprogress .bar').data('resize'));
                    $(base_widget).find('.fileprogress .bar').attr('aria-label',
                        $(base_widget).find('.fileprogress .bar').text());
                    $(base_widget).find('.fileprogress').show();
                    $(base_widget).find('.fileprogress .bar').show().focus();

                    var reader = new FileReader();
                    reader.onload = function(e) {
                        var original_image_64 = e.target.result;
                        var img = document.createElement("img");
                        img.onload = function() {
                            var adapt_image = function(orientation) {
                                /*
                                 * 1 = Horizontal (normal)
                                 * 2 = Mirror horizontal
                                 * 3 = Rotate 180
                                 * 4 = Mirror vertical
                                 * 5 = Mirror horizontal and rotate 270 CW
                                 * 6 = Rotate 90 CW
                                 * 7 = Mirror horizontal and rotate 90 CW
                                 * 8 = Rotate 270 CW */
                                var canvas = document.createElement("canvas");
                                var ctx = canvas.getContext('2d');
                                ctx.drawImage(img, 0, 0);

                                var MAX_WIDTH = 2000;
                                var MAX_HEIGHT = 2000;
                                var width = img.width;
                                var height = img.height;

                                if (width > height && width > MAX_WIDTH) {
                                    height *= MAX_WIDTH / width;
                                    width = MAX_WIDTH;
                                } else if (height > MAX_HEIGHT) {
                                    width *= MAX_HEIGHT / height;
                                    height = MAX_HEIGHT;
                                }
                                if (img.width != width || img.height != height) {
                                    canvas.width = width;
                                    canvas.height = height;
                                    var ctx = canvas.getContext('2d');
                                    switch (orientation) {
                                        case 1:
                                            break;
                                        case 2:
                                            ctx.translate(width, 0);
                                            ctx.scale(-1, 1);
                                            break;
                                         case 3:
                                             ctx.translate(width, height);
                                             ctx.rotate(180 / 180 * Math.PI);
                                             break;
                                         case 4:
                                             ctx.translate(0, height);
                                             ctx.scale(1, -1);
                                             break;
                                         case 5:
                                             canvas.width = height;
                                             canvas.height = width;
                                             ctx.rotate(90 / 180 * Math.PI);
                                             ctx.scale(1, -1);
                                             break;
                                         case 6:
                                             canvas.width = height;
                                             canvas.height = width;
                                             ctx.rotate(-90 / 180 * Math.PI);
                                             ctx.translate(-width, 0);
                                             break;
                                         case 7:
                                             canvas.width = height;
                                             canvas.height = width;
                                             ctx.rotate(270 / 180 * Math.PI);
                                             ctx.translate(-width, height);
                                             ctx.scale(1, -1);
                                             break;
                                         case 8:
                                             canvas.width = height;
                                             canvas.height = width;
                                             ctx.translate(height, 0);
                                             ctx.rotate(-270 / 180 * Math.PI);
                                             break;
                                    }
                                    ctx.drawImage(img, 0, 0, width, height);
                                    var new_image_64 = canvas.toDataURL('image/jpeg', 0.95);
                                    var blob = null;
                                    if (data.files[0].type == 'image/jpeg') {
                                        blob = ExifRestorer.restore_as_blob(original_image_64, new_image_64);
                                        blob.name = data.files[0].name;
                                    } else {
                                        // adapted from dataURItoBlob, from
                                        // https://stackoverflow.com/questions/12168909/blob-from-dataurl#12300351
                                        var byteString = atob(new_image_64.split(',')[1]);
                                        var mimeString = new_image_64.split(',')[0].split(':')[1].split(';')[0]
                                        var ab = new ArrayBuffer(byteString.length);
                                        var ia = new Uint8Array(ab);
                                        for (var i = 0; i < byteString.length; i++) {
                                            ia[i] = byteString.charCodeAt(i);
                                        }
                                        blob = new Blob([ab], {type: mimeString});
                                        blob.name = data.files[0].name + '.jpg';
                                    }
                                    data.files[0] = blob;
                                }
                                $(base_widget).find('.file-button').trigger('wcs:image-blob', data.files[0]);
                                return $.WcsFileUpload.upload(base_widget, data);
                            };
                            if (data.files[0].type == 'image/jpeg') {
                                EXIF.getData(img, function () {
                                    var orientation = +EXIF.getTag(this, "Orientation");
                                    adapt_image(orientation);
                                });
                            } else {
                                adapt_image(0);
                            }
                        }
                        img.src = e.target.result;
                    }
                    reader.readAsDataURL(data.files[0]);
                } else {
                  return $.WcsFileUpload.upload(base_widget, data);
                }
            },
            done: function(e, data) {
                if (data.result[0].error) {
                    $(base_widget).find('.fileprogress').addClass('upload-error');
                    $(base_widget).find('.fileprogress .bar').text(data.result[0].error);
                    $(base_widget).find('.fileprogress .bar').attr('aria-label',
                        $(base_widget).find('.fileprogress .bar').text());
                    $(base_widget).find('.fileprogress .bar').focus();
                    return;
                }
                $(base_widget).find('.fileprogress').hide();
                $.WcsFileUpload.set_file(base_widget, data.result[0]);
                $(base_widget).find('[type=file]').trigger('change');
                $(base_widget).find('input[type=text]').trigger('change');
                var $remove_button = $(base_widget).find('.remove');
                $remove_button.find('.remove').focus();
            },
            fail: function(e, data) {
                $(base_widget).find('.fileprogress').addClass('upload-error');
                $(base_widget).find('.fileprogress .bar').text(
                        $(base_widget).find('.fileprogress .bar').data('error'));
                $(base_widget).find('.fileprogress .bar').attr('aria-label',
                    $(base_widget).find('.fileprogress .bar').text());
                $(base_widget).find('.fileprogress .bar').focus();
            },
            progress: function (e, data) {
                var progress = parseInt(data.loaded / data.total * 100, 10);
                $(base_widget).find('.fileprogress')[0].style.setProperty('--upload-progression', progress + '%');
                $(base_widget).find('.fileprogress .bar').attr('aria-valuenow', progress).attr('aria-valuetext', progress + '%');
            }
        });
        $(this).find('.remove').click(function() {
            $(base_widget).find('input[type=text]').val('');
            $(base_widget).find('.fileinfo').hide();
            $(base_widget).find('input[type=file]').show();
            $(base_widget).find('.use-file-from-fargo').show();
            $(base_widget).removeClass('has-file').addClass('has-no-file');
            $(base_widget).find('input[type=file]').trigger('change');
            $(base_widget).find('input[type=text]').trigger('change');
            $(base_widget).find('input[type=file]').focus();
            return false;
        });
        $(this).find('a.change').click(function() {
            $(base_widget).find('input[type=file]').click();
            return false;
        });
    },

    upload: function(base_widget, data) {
        var max_file_size = $(base_widget).find('input[type=file]').data('max-file-size');
        if (max_file_size && data.files[0].size > max_file_size) {
                var message = WCS_I18N.file_size_error;
                var max_file_size_human = $(base_widget).find('input[type=file]').data('max-file-size-human');
                if (max_file_size_human) {
                    message = message + ' (' + max_file_size_human + ')';
                }
                $(base_widget).find('.fileprogress').addClass('upload-error');
                $(base_widget).find('.fileprogress .bar').text(message);
                $(base_widget).find('.fileprogress').show();
                return;
        }
        $(base_widget).find('.fileprogress').removeClass('upload-error');
        $(base_widget).find('.fileprogress .bar').text(
        $(base_widget).find('.fileprogress .bar').data('upload'));
        $(base_widget).find('.fileprogress')[0].style.setProperty('--upload-progression', '0%');
        $(base_widget).find('.fileprogress').show();
        $(base_widget).find('.fileinfo').hide();
        $(base_widget).parents('form').find('div.buttons button').prop('disabled', true);
        var jqXHR = data.submit();
    },

    set_file: function(base_widget, data) {
        if (! data) return;
        var base_file_name = data.name;
        if (data.url) {
            $(base_widget).find('.filename').empty().append(
                $('<a>', {href: data.url, download: base_file_name, text: base_file_name}));
        } else {
            $(base_widget).find('.filename').text(base_file_name);
        }
        var $remove_button = $(base_widget).find('.remove');
        if ($remove_button.length) {
            $remove_button.attr('title', $remove_button[0].dataset.titlePrefix + ' ' + base_file_name);
        }
        $(base_widget).find('.fileinfo').show();
        $(base_widget).find('input[type=text]').val(data.token);
        $(base_widget).parents('form').find('div.buttons button').prop('disabled', false);
        $(base_widget).find('[type=file]').hide();
        $(base_widget).find('.use-file-from-fargo').hide();
        $(base_widget).addClass('has-file').removeClass('has-no-file');

        $.WcsFileUpload.image_preview(base_widget, data.token);
    },

    image_preview: function(base_widget, img_token) {
        var file_button = $(base_widget).find('.file-button');
        if(file_button.hasClass("file-image")) {
            file_button[0].style.setProperty('--image-preview-url', `url(${window.location.protocol}//${window.location.host}${window.location.pathname}tempfile?t=${img_token}&thumbnail=1)`);
        }
    }
}
