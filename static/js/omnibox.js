
$(document).ready(function() {
  prepareOmnibox("cu");
});

function omnibox(ele, types, options) {

    if (ele.data('select2')) {
        return;
    }
    var data = [];

    if (options === undefined) {
        options = {}
    }

    if (options.variables) {
        for (var idx in options.variables) {
            var v = '@' + options.variables[idx].name.toLowerCase();
            data.push({id: v, text: v + " - " + options.variables[idx].display})
        }
    }

    if (types === undefined) {
        types = 'cg';
    }

    var placeholder = null;
    if (types == 'g'){
        placeholder = gettext("Enter one or more contact groups");
    } else if (types == 'cu'){
        placeholder = gettext("Recipients, enter contacts or phone numbers");
    }
    else {
        placeholder = gettext("Recipients, enter contacts or groups");
    }

    ele.attr('placeholder', placeholder);

    var q = '';
    if (!options.createSearchChoice && types.indexOf('u') >= 0) {
        options.createSearchChoice = arbitraryNumberOption;
    }

    var multiple = true;
    if (options.multiple != undefined) {
        multiple = options.multiple;
    }

    return ele.removeClass("loading").select2({
        placeholder: placeholder,
        data: data,
        allowClear: false,
        selectOnBlur: false,
        minimumInputLength: 0,
        multiple: multiple,
        initSelection : function (element, callback) {
            var initial = $(element).val();
            element.select2('data', []);
            if (initial) {
                initial = eval(initial);
                callback(initial);
            }
        },
        createSearchChoice: options.createSearchChoice,
        ajax: {
            url: "/contact/omnibox/?types=" + types,
            dataType: 'json',
            data: function (term, page, context) {
                q = term;
                return {
                    search: term,
                    page: page
                };
            },
            results: function (response, page, context) {
                if (data) {
                    if (q) {
                        q = q.toLowerCase();
                        if (q.indexOf('@') == 0) {
                            for (var idx in data) {
                                var variable = data[idx];
                                if (variable.id.indexOf(q) == 0) {
                                    response.results.unshift(variable);
                                }
                            }
                        }
                    }
                }
                return response;
            }
        },
        escapeMarkup: function(m) {
            return m;
        },
        containerCssClass: "omnibox-select2",
        formatSelection:formatOmniboxSelection,
        formatResult:formatOmniboxOption
    });
}

function prepareOmnibox(types) {
    if (types === undefined) {
        types = 'cg';
    }
    omnibox($(".omni_widget"), types);
}

function initializeOmnibox(initial) {
    var options = {
        placeholder: gettext("Recipients, enter contacts or phone numbers"),
        minimumInputLength: 0,
        multiple: true,
        ajax: {
            url: "/contact/omnibox/?types=cu",
            dataType: 'json',
            data: function (term, page) {
                return {
                    search: term,
                    page: page
                };
            },
            results: function (data, page) {
                return data;
            }
        },
        escapeMarkup: function(m) {
            return m;
        },
        containerCssClass: "omnibox-select2",
        formatSelection: formatOmniboxSelection,
        formatResult: formatOmniboxOption,
        createSearchChoice: arbitraryNumberOption
    };

    var omnibox = $("#omnibox").removeClass("loading").select2(options);

    // if we have some initial data set it
    if (initial) {
        $("#omnibox").select2('data', initial);
        $("#omni-select2").show();
        $("#loading").hide();
        $("#send-message .ok").text(gettext("Send Message")).removeClass("disabled");
    }
    // otherwise, make sure our data is cleared
    else {
        $("#omnibox").select2('data', null);
    }
}

function arbitraryNumberOption(term, data) {
    if (anon_org){
      return null;
    }

    if ($(data).filter(function() { return this.text.localeCompare(term)===0; }).length===0) {
        if (!isNaN(parseFloat(term)) && isFinite(term)) {
            return {id:"n-" + term, text:term};
        }
    }
}

function formatOmniboxSelection(item) {
    if (item.length == 0) {
      return "";
    }

    return formatOmniboxItem(item);
}

function formatOmniboxOption(item, container, query) {
    // support also numbers witch are entered in the omnibox with a plus sign
    if (query.term[0] == "+") {
        query.term = query.term.substring(1, query.length);
    }
    return formatOmniboxItem(item);
}

function formatOmniboxItem(item) {
    var text = (item.extra != null) ? (item.text + " (" + item.extra + ")") : item.text;
    var clazz = '';

    if (item.id.indexOf("g-") == 0) {
        clazz = 'omni-group';
    } else if (item.id.indexOf("c-") == 0) {
        clazz = 'omni-contact';
    } else if (item.id.indexOf("u-") == 0) {
        if (item.scheme == 'tel') {
            clazz = 'omni-tel';
        } else if (item.scheme == 'twitter') {
            clazz = 'omni-twitter';
        } else if (item.scheme == 'telegram') {
            clazz = 'omni-telegram';
        }
    }

    return '<div class="omni-option ' + clazz + '">' + text + '</div>';
}
