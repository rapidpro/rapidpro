function buildTriggerFlowParams(params = [], values = []) {
    const embedContainer = document.getElementById('embed-container-keyword');
    embedContainer.innerHTML = '';

    if (params.length > 0) {
        for (var param in params) {
            var embedRow = document.createElement('div');
            embedRow.className = 'embed-row embed-header-keyword';

            var embedField = document.createElement('div');
            embedField.className = 'embed-field-keyword';

            var embedFieldInput = document.createElement('input');
            embedFieldInput.type = 'text';
            embedFieldInput.name = 'flow_parameter_field_' + param;
            embedFieldInput.readOnly = true;
            embedFieldInput.value = params[param];

            var embedValue = document.createElement('div');
            embedValue.className = 'embed-value-keyword';

            embedField.append(embedFieldInput);

            var embedValueInput = document.createElement('input');
            embedValueInput.className = 'flow_parameter_value';
            embedValueInput.name = 'flow_parameter_value_' + param;
            embedValueInput.type = 'text';
            embedValueInput.required = true;

            if (values[param]) {
                embedValueInput.value = values[param];
            }

            var embedValueErrorMsg = document.createElement('div');
            embedValueErrorMsg.className = 'embed-error-message embed-error-message-value';

            embedValue.append(embedValueInput);
            embedValue.append(embedValueErrorMsg);

            embedRow.append(embedField);
            embedRow.append(embedValue);

            embedContainer.append(embedRow);
        }

        $('#embedded-data').show();
        $('h5.embedded-data').show();
        $('#embed-container-keyword').show();
        $('.embed-header-keyword').show();
    } else {
        $('#embedded-data').hide();
        $('h5.embedded-data').hide();
        $('#embed-container-keyword').hide();
        $('.embed-header-keyword').hide();
    }
}

function validateFlowParams(form) {
    var error = false;
    var flowParamsValues = form.find('input.flow_parameter_value');
    flowParamsValues.each(function() {
        var element = $(this);
        var trimValue = $.trim(element.val());
        if (!trimValue.length) {
            element.addClass('invalid');
            element.parent().parent().find('.embed-error-message-value').html('The value is required');
            error = true;
        } else {
            element.removeClass('invalid');
            element.parent().parent().find('.embed-error-message-value').html('');
        }
    });
    return error;
}