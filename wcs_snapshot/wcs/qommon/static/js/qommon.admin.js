$(function() {
    $('h4.foldable').click(function() {
       $(this).toggleClass('folded').next().toggle();
    });
    $('h4.foldable.folded').next().hide();

    // watch gadjo un/folding section and save state to user preferences
    const foldableClassObserver = new MutationObserver((mutations) => {
      mutations.forEach(mu => {
        const old_folded_status = (mu.oldValue.indexOf('folded') != -1)
        const new_folded_status = mu.target.classList.contains('folded')
        if (old_folded_status == new_folded_status) return
        var pref_message = Object();
        pref_message[mu.target.dataset.sectionFoldedPrefName] = new_folded_status
        fetch('/api/user/preferences', {
            method: 'POST',
            headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
            body: JSON.stringify(pref_message)
        })
      })
    })
    document.querySelectorAll('[data-section-folded-pref-name]').forEach(
      el => foldableClassObserver.observe(el, {attributes: true, attributeFilter: ['class'], attributeOldValue: true})
    )

    $('fieldset.foldable legend').click(function() {
       $(this).parent().toggleClass('folded');
    });

    $('option[data-goto-url]').parents('select').on('change', function() {
      var jumpto_button = this.jumpto_button;
      if (typeof jumpto_button == 'undefined') {
        this.jumpto_button = $('<a>', {'class': 'button button--go-to-option', text: '↗'});
        this.jumpto_button.hide();
        if ($(this).attr('aria-hidden')) { // select2
          this.jumpto_button.insertAfter($(this).next());
        } else {
          this.jumpto_button.insertAfter($(this));
        }
      }
      var option = $(this).find('option:selected');
      var data_url = option.data('goto-url');
      if (data_url) {
        this.jumpto_button.attr('href', data_url);
        this.jumpto_button.show();
      } else {
        this.jumpto_button.hide();
      }
    }).trigger('change');
    $('[data-filter-trigger-select]').on('change', function() {
      var option = $(this).val();
      $('#form_trigger_id option').each(function(idx, elem) {
         if (elem.dataset.slugs === undefined) return;
         var option_slugs = elem.dataset.slugs.split('|');
         if (option == '' || option_slugs.indexOf(option) == -1) {
           $(elem).hide();
         } else {
           $(elem).show();
         }
      });
      if ($('#form_trigger_id option:selected')[0].style.display == 'none') {
        $('#form_trigger_id').val('');
      }
    });
    if ($('#form_trigger_id').length) {
      var current_objectdef = $('[data-filter-trigger-select]').val();
      var current_val = $('#form_trigger_id').val();
      $('[data-filter-trigger-select]').trigger('change');
      if (current_val) {  // may have been reset when filtering
        $('#form_trigger_id option').each(function(idx, elem) {
         if (elem.dataset.slugs === undefined) return;
          var option_slugs = elem.dataset.slugs.split('|');
          if (elem.value == current_val && option_slugs.indexOf(current_objectdef) !== -1) {
            $('#form_trigger_id')[0].selectedIndex = idx;
          }
        });
      }
    }

    /* focus tab with error */
    $('.form-with-tabs .error').first().closest('[role=tabpanel]').each(function(idx, elem) {
      const $tab_button = $('[role=tab][aria-controls="' + $(elem).attr('id') + '"]');
      $(elem).closest('.pk-tabs')[0].tabs.selectTab($tab_button[0]);
    });
    $('.form-with-tabs [role=tabpanel]').on('gadjo:tab-selected', function() {
      $(this).find('.qommon-map').trigger('qommon:invalidate');
    });

    /* insert variable code in textarea when clicking on them */
    var latest_textarea = null;
    if ($('textarea').length == 1) {
      latest_textarea = $('textarea');
    }
    $('textarea').on('focus', function() { latest_textarea = this; });
    $('#substvars td:nth-child(2)').css('cursor', 'pointer').click(function() {
       if (latest_textarea === null) return true;
       var current_val = $(latest_textarea).val();
       position = $(latest_textarea).get(0).selectionStart;
       if (position >= 0) {
         var code = $(this).text();
         var new_val = current_val.substr(0, position) + code + current_val.substr(position);
         $(latest_textarea).val(new_val);
       }
       return true;
    });

    /* open theme preview in a iframe */
    $('a.theme-preview').click(function() {
        var html = '<div id="theme-preview"><iframe src="' + $(this).attr('href') + '"></iframe></div>';
        var title = $(this).parent().parent().find('label').text()
        var dialog = $(html).dialog({
                closeText: WCS_I18N.close,
                modal: true,
                title: title,
                width: $(window).width() - 180,
                height: $(window).height() - 80
        });
        return false;
    });

    /* highlight info text on hover */
    $('.action-info-text[data-button-name]').each(function(idx, elem) {
        $('[name=' + $(elem).data('button-name') + ']').on('mouseenter', function() {
           $(elem).addClass('highlight');
        }).on('mouseleave', function() {
           $(elem).removeClass('highlight');
        });
    });

    // "live" update on condition widget
    $('div[data-validation-url]').each(function(idx, elem) {
      var $widget = $(this);
      var widget_name = $widget.find('input').attr('name');
      var prefix = widget_name.substr(0, widget_name.lastIndexOf('$')) + '$';
      $(this).find('input, select').on('blur', function() {
        var data = Object();
        $widget.find('select, input').each(function(idx, elem) {
          data[$(elem).attr('name').replace(prefix, '')] = $(elem).val();
        });
        data['warn-on-datetime'] = ($('#form_timeout').length && ! $('#form_timeout').val());
        $.ajax({
          url: $widget.data('validation-url'),
          data: data,
          dataType: 'json',
          success: function(data) {
            var $error = $widget.find('.error');
            if ($error.length == 0) {
               $error = $('<div class="error"></div>');
               $error.appendTo($widget);
            }
            if (data.msg) {
              $error.text(data.msg);
            } else {
              $error.remove();
            }
          }
        });
        return false;
      });
    });

    $('#journal-filter #form_object').on('change', function() {
      if (this.value) {
        $('[data-widget-name="object_id"]').show();
      } else {
        $('[data-widget-name="object_id"]').hide();
      }
    });

    /* keep title/slug in sync */
    $('body').delegate('input[data-slug-sync]', 'input change paste',
        function() {
            var $slug_field = $(this).parents('form').find('[name=' + $(this).data('slug-sync') + ']');
            if ($slug_field.prop('readonly')) return;
            var slug_value = window.slugify($(this).val());
            if (slug_value && ! slug_value.match(/^[a-z]/)) {
              slug_value = "n" + slug_value;
            }
            $slug_field.val(slug_value);
    });

    /* remove readonly attribute from fields */
    $('body').delegate('a.change-nevertheless', 'click', function(e) {
      var readonly_fields = $(this).parents('form').find('input[readonly]');
      if (readonly_fields.length) {
        readonly_fields.prop('readonly', false);
        readonly_fields[0].focus();
      }
      $(this).parent().hide();
      return false;
    });

    /* submission channel */
    $('div.submit-channel-selection').show().find('select').on('change', function() {
      $('input[type=hidden][name=submission_channel]').val($(this).val());
    });

    /* user id */
    $('select.user-selection').each(function(idx, elem) {
      var user_select2_api_url = '/api/users/';
      var user_select2_api_roles_param = $(elem).data('users-api-roles') || '';
      const user_select2_options = {
        language: {
          errorLoading: function() { return WCS_I18N.s2_errorloading; },
          noResults: function () { return WCS_I18N.s2_nomatches; },
          inputTooShort: function (input, min) { return WCS_I18N.s2_tooshort; },
          loadingMore: function () { return WCS_I18N.s2_loadmore; },
          searching: function () { return WCS_I18N.s2_searching; }
        },
        ajax: {
          delay: 250,
          dataType: 'json',
          data: function(params) {
            return {q: params.term, roles: user_select2_api_roles_param, limit: 10};
          },
          processResults: function (data, params) {
            return {results: data.data};
          },
          url: user_select2_api_url
        },
        placeholder: '-',
        allowClear: true,
        templateResult: function (state) {
          if (!state.description) {
            return state.text;
          }
          var $template_string = $('<span>');
          $template_string.append(
                  $('<span>', {text: state.text})).append(
                  $('<br>')).append(
                  $('<span>' + state.description + '</span>'));
          return $template_string;
        }
      }
      $(elem).select2(user_select2_options);
      $(elem).on('select2:open', function (e) {
        var available_height = $(window).height() - $(this).offset().top;
        $('ul.select2-results__options').css('max-height', (available_height - 100) + 'px');
      });
    });
    $('div.submit-user-selection').show().find('select').on('change', function() {
      $('input[type=hidden][name=user_id]').val($(this).val());
      $('form[data-live-url]').trigger(
          'wcs:change',
          {modified_field: 'user', selected_user_id: $(this).val()}
      );
    });

    /* new action form */
    $('#new-action-form select').on('change', function() {
      if ($(this).val() == '') {
        $('#new-action-form select').prop('disabled', null)
      } else {
        $('#new-action-form select').prop('disabled', 'disabled')
        $(this).prop('disabled', null)
      }
      return false;
    });

    /* possibility to toggle the sidebar */
    if ($('#sidebar').length) {
      $('#sidebar-toggle').click(function() {
        if ($('#sticky-sidebar').css('display') === 'none') {
          $('#sidebar').animate(
              {'max-width': '24rem'},
              400,
              function() {
                $('#sticky-sidebar').show()
                $(window).trigger('wcs:sidebar-toggled');
              }
          );
        } else {
          $('#sticky-sidebar').hide();
          $('#sidebar').animate(
              {'max-width': '0rem'},
              400,
              function() {
                $(window).trigger('wcs:sidebar-toggled');
              }
          );
        }
      });
    }

    /* load html parts asynchronously */
    $('[data-async-url]').each(function(idx, elem) {
      $(elem).load($(elem).data('async-url'));
    });

    /* keep sidebar sticky */
    if ($('#sticky-sidebar').length) {
      var $window = $(window);
      var sidebar_fixed_from = $('#sticky-sidebar').offset().top;
      var sidebar_top = $('#sticky-sidebar').position().top;
      $window.bind('scroll', function() {
        var working_bar_height = 0;
        if ($('body[data-environment-label]').length) {
          working_bar_height = 20;
        }
        var pos = $window.scrollTop();
        var minus = 0;
        if (pos >= sidebar_fixed_from) {
          $('#sticky-sidebar').css('top', pos - (sidebar_fixed_from - sidebar_top));
        } else {
          $('#sticky-sidebar').css('top', 'auto');
          minus = sidebar_fixed_from - pos;
        }
        $('#sticky-sidebar').css('height', 'calc(100vh - 5px - ' + (minus + working_bar_height) + 'px)');
      });
      $window.trigger('scroll');
    }

    if (document.querySelector('.preview-payload-structure a')) {
      $('div.WidgetDict[data-widget-name*="post_data"] input[type="text"]').on('change', function() {
        var $widget = $(this).parents('div.WidgetDict');
        if ($(this).parents('div.dict-key').length < 0) return;
        if (!$(this).val().includes('/')) return;
        var $preview_payload_structure = $widget.find('.preview-payload-structure')
        $preview_payload_structure.removeAttr('hidden')
      }).trigger('change');
      document.querySelector('.preview-payload-structure a').addEventListener('click', function(event) {
        var $widget = $(this).parents('div.WidgetDict')
        fetch('/api/preview-payload-structure', {
          method: 'POST',
          body: new URLSearchParams($('input', $widget).serialize())
        }).then((response) => {
          if (! response.ok) {
            return
          }
          return response.text()
        }).then((text) => {
          this.parentElement.querySelector('.preview-payload-structure--content').innerHTML = text
          this.parentElement.querySelector('dialog').showModal()
        })
        return false
      })
      document.querySelector('.preview-payload-structure .dialog-close-button').addEventListener('click', (event) => {
        event.preventDefault();
        document.querySelector('.preview-payload-structure dialog').close()
      })
    }

    $('#inspect-test-tools form').on('submit', function() {
      var data = $(this).serialize();
      var jqxhr = $.ajax({url: 'inspect-tool',
          xhrFields: { withCredentials: true },
          data: $(this).serialize(),
          method: 'POST',
          async: true,
          dataType: 'html',
          success: function(data) {
            var form_token = jqxhr.getResponseHeader('X-form-token');
            $('input[name="_form_id"]').val(form_token);
            $('#test-tool-result').empty().append($(data));
          }
      });
      return false;
    });
    $('#inspect-test-tools textarea').on('keydown', function(e) {
      if ((e.ctrlKey || e.metaKey) && (e.keyCode == 13 || e.keyCode == 10)) {
        $(this).parents('form').trigger('submit');
        return false;
      }
      return true;
    });
    $('#inspect-variables').on('click', '.inspect-expand-variable', function() {
      var href= this.href;
      $('#inspect-variables').load(href + ' #inspect-variables > ul');
      return false;
    });

    if ($('svg').length && typeof(svgPanZoom) === 'function') {
        var panned_svg = svgPanZoom('svg', {controlIconsEnabled: true});
        $(window).on('resize wcs:sidebar-toggled gadjo:sidepage-toggled', function() {
            panned_svg.resize();
        });
    }

    $('[type=radio][name=display_mode]').on('change', function() {
      // show everything
      $('select[name="data_source$type"] option').show();
      $('input[name="data_mode"][value="simple-list"]').prop('disabled', false);
      // then restrict
      if ($(this).val() == 'map') {
        $('input[name="data_mode"][value="simple-list"]').prop('disabled', true);
        $('input[name="data_mode"][value="data-source"]').click()
        $('select[name="data_source$type"] option:not([data-type="geojson"])').hide();
        if ($('select[name="data_source$type"] option:selected').css('display') != 'block') {
          // if current option is not visible (not a geojson source) select
          // first appropriate source.
          for (const option of $('select[name="data_source$type"] option')) {
            if ($(option).css('display') == 'block') {
              $(option).prop('selected', true).trigger('change');
              break;
            }
          }
        }
        if ($('select[name="data_source$type"] option:selected').css('display') != 'block') {
          // still empty, it means there are no geojson sources at all, display
          // first option (which will be "None").
          $('select[name="data_source$type"] option').first().show().prop('selected', true);
        }
      }
      if ($(this).val() == 'timetable') {
        $('input[name="data_mode"][value="simple-list"]').prop('disabled', true);
        $('input[name="data_mode"][value="data-source"]').click()
        $('select[name="data_source$type"] option:not([data-maybe-datetimes="true"])').hide();
        if ($('select[name="data_source$type"] option:selected:visible').length == 0) {
          $('select[name="data_source$type"] option:visible').first().prop('selected', true).trigger('change');
        }
      }
      if ($(this).val() == 'images') {
        $('input[name="data_mode"][value="simple-list"]').prop('disabled', true);
        $('input[name="data_mode"][value="data-source"]').click()
        $('select[name="data_source$type"] option:not([data-has-image="true"])').hide();
        if ($('select[name="data_source$type"] option:selected:visible').length == 0) {
          $('select[name="data_source$type"] option:visible').first().prop('selected', true).trigger('change');
        }
      }
    });
    $('[type=radio][name=display_mode]:checked').trigger('change');

    if (document.getElementById('validation-error-messages')) {
      const error_messages = JSON.parse(document.getElementById('validation-error-messages').textContent);
      const error_message_widget = document.getElementById('form_validation__error_message');
      $('#form_validation__type').on('change', function() {
        var current_message = error_message_widget.value;
        var new_message = error_messages[$(this).val()];
        if (! current_message || Object.values(error_messages).indexOf(current_message) != -1) {
          error_message_widget.value = new_message || '';
        }
      }).trigger('change');
    }

    $('#form_prefill__locked').on('change', function() {
      $('#form_prefill__locked-unless-empty').attr('disabled', ! $(this).prop('checked'))
    }).trigger('change')

    function prepate_journal_links() {
      $('#journal-page-links a').on('click', function() {
        var url = $(this).attr('href');
        $.ajax({url: url, dataType: 'html', success: function(html) {
          var $html = $(html);
          var $table = $html.find('#journal-table');
          var $page_links = $html.find('#journal-page-links');
          if ($table.length && $page_links.length) {
            $('#journal-table').replaceWith($table);
            $('#journal-page-links').replaceWith($page_links);
            prepate_journal_links();
            if (window.history) {
              window.history.replaceState(null, null, url);
            }
          }
        }});
        return false;
      });
    }
    prepate_journal_links();

    // IE doesn't accept periods or dashes in the window name, but the element IDs
    // we use to generate popup window names may contain them, therefore we map them
    // to allowed characters in a reversible way so that we can locate the correct
    // element when the popup window is dismissed.
    function id_to_windowname(text) {
        text = text.replace(/\./g, '__dot__');
        text = text.replace(/\-/g, '__dash__');
        return text;
    }

    function windowname_to_id(text) {
        text = text.replace(/__dot__/g, '.');
        text = text.replace(/__dash__/g, '-');
        return text;
    }

    function showPopup(triggeringLink, name_regexp) {
        var name = triggeringLink.id.replace(name_regexp, '');
        name = id_to_windowname(name);
        var href = triggeringLink.href;
        var win = window.open(href, name, 'height=500,width=800,resizable=yes,scrollbars=yes');
        win.focus();
        return false;
    }

    function showRelatedObjectPopup(triggeringLink) {
        return showPopup(triggeringLink, /^(edit|add)_/);
    }

    function dismissRelatedObjectPopup(win, newId, newRepr, edit_related_url, view_related_url) {
        var name = windowname_to_id(win.name);
        var elem = document.getElementById(name);
        if (elem) {
            var elemName = elem.nodeName.toUpperCase();
            if (elemName === 'SELECT') {
                var $option = $('<option />').val(newId).html(newRepr).prop('selected', true);
                if (edit_related_url) {
                    $option.attr('data-edit-related-url', edit_related_url);
                }
                if (view_related_url) {
                    $option.attr('data-view-related-url', view_related_url);
                }
                $(elem).append($option);
            }
            // Trigger a change event to update related links if required.
            $(elem).trigger('change');
        }
        win.close();
    }
    window.dismissRelatedObjectPopup = dismissRelatedObjectPopup;

    $('body').on('click', '.add-related, .edit-related', function(e) {
        e.preventDefault();
        if (this.href) {
            showRelatedObjectPopup(this);
        }
    });

    const other_field_varnames_element = document.getElementById('other-fields-varnames')
    const varname_field_widget = document.getElementById('form_varname')
    if (other_field_varnames_element && varname_field_widget) {
      const other_field_varnames = JSON.parse(other_field_varnames_element.textContent)
      const message_span = document.querySelector('#form_varname + .inline-hint-message');
      ['keyup', 'change'].forEach(event_type =>
        varname_field_widget.addEventListener(event_type, function(event) {
          if (other_field_varnames.indexOf(this.value) != -1) {
            message_span.style.display = 'inline-block'
          } else {
            message_span.style.display = 'none'
          }
        })
      )
      varname_field_widget.dispatchEvent(new Event('keyup'))
    }

    const common_varnames_element = document.getElementById('common-varnames')
    const condition_widget = document.getElementById('form_condition__value_django')
    if (common_varnames_element && condition_widget) {
      const common_varnames = JSON.parse(common_varnames_element.textContent)
      const referenced_varnames_re = /\b(?:form)[_\.]var[_\.]([a-zA-Z0-9_]+?)(?:_raw|_live_|_structured_|_var_|\b)/g
      const message_container = document.querySelector('[data-widget-name="condition"] .content')
      const message_p = document.createElement('p')
      message_p.classList.add('inline-hint-message')
      message_p.style.display = 'none'
      message_container.appendChild(message_p)
      ;['change'].forEach(event_type =>
        condition_widget.addEventListener(event_type, function(event) {
          unmatched = Array()
          for (match of this.value.matchAll(referenced_varnames_re)) {
            if (common_varnames.indexOf(match[1]) == -1) {
              unmatched.push(match[1])
            }
          }
          if (unmatched.length) {
            message_p.textContent = WCS_I18N.warn_condition_maybe_unknown_varname + ' (' + unmatched.join(', ') + ')'
            message_p.style.display = 'block'
          } else {
            message_p.style.display = 'none'
          }
        })
      )
      condition_widget.dispatchEvent(new Event('change'))
    }

    const compact_table_dataview_switch = document.querySelector('#compact-table-dataview-switch input')
    if (compact_table_dataview_switch) {
      compact_table_dataview_switch.addEventListener('change', function(event) {
        document.querySelectorAll('.dataview').forEach(function(el) {
          if (compact_table_dataview_switch.checked) {
            el.classList.add('compact-dataview')
          } else {
            el.classList.remove('compact-dataview')
          }
        })
        var pref_message = Object()
        pref_message['use-compact-table-dataview'] = compact_table_dataview_switch.checked
        fetch('/api/user/preferences', {
          method: 'POST',
          headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
          body: JSON.stringify(pref_message)
        })
      })
      if (compact_table_dataview_switch.checked && ! document.querySelector('.dataview.compact-dataview')) {
       compact_table_dataview_switch.dispatchEvent(new Event('change'))
      }
    }

    document.querySelectorAll('.toggle-escape-button').forEach(
      el => el.addEventListener('click', (event) => {
        event.preventDefault()
        el.parentNode.classList.toggle('display-codepoints')
      })
    )

    const documentation_block = document.querySelector('.bo-block.documentation')
    const editor = document.getElementById('documentation-editor')
    const editor_link = document.querySelector('.focus-editor-link')
    const title_byline = document.querySelector('.object--status-infos')
    const documentation_save_button = document.querySelector('.bo-block.documentation button.save')
    var clear_documentation_save_marks_timeout_id = null
    if (editor_link) {
      documentation_save_button.addEventListener('click', (e) => {
          editor.sourceContent = editor.getHTML()
          var documentation_message = Object()
          documentation_message['content'] = editor.sourceContent.innerHTML
          document.querySelector('.documentation-save-marks .mark-error').style.visibility = 'hidden'
          document.querySelector('.documentation-save-marks .mark-success').style.visibility = 'hidden'
          document.querySelector('.documentation-save-marks .mark-sent').style.visibility = 'visible'
          fetch(`${window.location.pathname}update-documentation`, {
            method: 'POST',
            headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
            body: JSON.stringify(documentation_message)
          }).then((response) => {
            if (! response.ok) {
              return
            }
            return response.json()
          }).then((json) => {
            if (json && json.err == 0) {
              if (json.changed) {
                document.querySelector('.documentation-save-marks .mark-success').style.visibility = 'visible'
              } else {
                document.querySelector('.documentation-save-marks .mark-sent').style.visibility = 'hidden'
                document.querySelector('.documentation-save-marks .mark-success').style.visibility = 'hidden'
              }
              if (json.empty) {
                document.querySelector('.bo-block.documentation').setAttribute('hidden', 'hidden')
              }
            } else {
              document.querySelector('.documentation-save-marks .mark-error').style.visibility = 'visible'
            }
            if (clear_documentation_save_marks_timeout_id) clearTimeout(clear_documentation_save_marks_timeout_id)
            clear_documentation_save_marks_timeout_id = setTimeout(
              function() {
                document.querySelector('.documentation-save-marks .mark-error').style.visibility = 'hidden'
                document.querySelector('.documentation-save-marks .mark-success').style.visibility = 'hidden'
                document.querySelector('.documentation-save-marks .mark-sent').style.visibility = 'hidden'
              }, 5000)
          })
        })

      editor_link.addEventListener('click', (e) => {
        e.preventDefault()
        if (editor_link.getAttribute('aria-pressed') == 'true') {
          editor.editable = false;
          documentation_save_button.dispatchEvent(new Event('click'))
          documentation_block.classList.remove('active')
          editor_link.setAttribute('aria-pressed', false)
          if (title_byline) title_byline.style.visibility = 'visible'
        } else {
          documentation_block.classList.add('active')
          document.querySelector('.bo-block.documentation').removeAttribute('hidden')
          if (document.querySelector('aside .bo-block.documentation')) {
            document.getElementById('sidebar').style.display = 'block'
            document.getElementById('sidebar').removeAttribute('hidden')
            if (document.getElementById('sticky-sidebar').style.display == 'none') {
              document.getElementById('sidebar-toggle').dispatchEvent(new Event('click'))
            }
          }
          if (title_byline) title_byline.style.visibility = 'hidden'
          editor_link.setAttribute('aria-pressed', true)
          editor.editable = true;
          editor.view.focus()
        }
      })
    }

    var session_job = document.querySelector('[data-job]')
    if (session_job) {
      var wait_job_id = setInterval(function() {
        fetch(`/afterjobs/${session_job.dataset.job}`, {
            headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
        }).then((response) => {
          if (! response.ok) {
            return
          }
          return response.json()
        }).then((json) => {
          if (json.status == 'completed') {
            session_job.classList.remove('info')
            session_job.classList.add('success')
            clearInterval(wait_job_id)
          } else if (json.status == 'failure') {
            session_job.classList.remove('info')
            session_job.classList.add('error')
            clearInterval(wait_job_id)
          }
        })
      }, 2000)
    }

  $('#appbar-search').on('change keyup', function() {
    var q = $(this).val();
    if (q) {
      q = window.slugify(q);
      $('.section').attr('data-has-hit', 'false');
      $('.objects-list li').each(function(idx, elem) {
        if ($(elem).attr('data-search-text').indexOf(q) > -1) {
          $(elem).parents('.section').attr('data-has-hit', 'true');
          $(elem).show();
        } else {
          $(elem).hide();
        }
      });
      $('.section[data-has-hit="true"]').show();
      $('.section[data-has-hit="false"]').hide();
    } else {
      $('.section').attr('data-has-hit', 'false');
      $('.section').show();
      $('.objects-list li').show();
    }
  }).trigger('keyup');

  $('#appbar-history-search').on('change keyup', function() {
    var q = $(this).val();
    if (q) {
      $('.snapshots-list tr').removeClass('collapsed')
      q = window.slugify(q)
      $('.snapshots-list tr').each(function(idx, elem) {
        if ($(elem).attr('data-search-text').indexOf(q) > -1) {
          $(elem).addClass('is-hit')
        } else {
          $(elem).addClass('is-not-hit')
        }
      });
    } else {
      $('.snapshots-list tr').removeClass('is-hit').removeClass('is-not-hit')
      $('.snapshots-list tr.initially-collapsed').addClass('collapsed')
    }
  }).trigger('keyup');

  if (document.getElementById('abort-job-button')) {
    const abort_button = document.getElementById('abort-job-button')
    const job_id = document.querySelector('span.afterjob').id
    abort_button.addEventListener('click', (event) => {
      abort_button.disabled = true
      fetch(`/afterjobs/${job_id}`, {
        method: 'POST',
        body: 'action=abort',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'}
      })
      return false
    })
  }

});
