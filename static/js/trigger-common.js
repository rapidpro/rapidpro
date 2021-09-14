function getPJAXContent(url, container, options) {
  let type = 'GET';
  let data;
  if (options) {
    if ('postData' in options) {
      type = 'POST';
      data = options['postData'];
    }
  }
  
  let headers = { 'X-PJAX': true };
  if (options && 'headers' in options) {
    headers = Object.keys(options).reduce(function(acc, key) {
      acc[key] = options['headers'][key]
      return acc;
    }, Object.assign(headers, {}));
  }
  
  document.dispatchEvent(new Event('temba-pjax-begin'));
  $.ajax({
    headers: headers,
    type: type,
    url: url,
    data: data,
    success: function(response, status, jqXHR) {
      options['onSuccess'](response, status, jqXHR);
      document.dispatchEvent(new Event('temba-pjax-complete'));
    }
  });
}

function submitFormData(options) {
  const { form, container, successFunc, appendToForm, postUrl } = options;
  const formData = [form.serialize()];
  if (appendToForm) formData.push(appendToForm);
  getPJAXContent(postUrl, container, {
    postData: formData.join('&'),
    headers: {
      'X-FORMAX': true
    },
    onSuccess: successFunc.bind(successFunc, form)
  });
}

function buildFormData(form) {
  return form.serializeArray().reduce(function(acc, item) {
    let value = item.value;
    if (item.name === 'omnibox' && value) {
      value = acc['omnibox'] || [];
      value.push(item.value);
    }
    acc[item.name] = value;
    return acc;
  }, {});
}

function getFLowName(flowContainer) {
  const flowDiv = document.querySelector(flowContainer).shadowRoot
  .querySelector('div.selected-item div.option-name');
  return flowDiv ? flowDiv.innerText : '--';
}

function getMessageTemplate() {
  return "Please confirm you'd like to proceed with the trigger for '{flowName}'";
}

function getTodaysDate() {
  return new Date().toJSON().substring(0,10);
}

function lockScheduleDate(formContainer) {
  const datePickerElem = formContainer.querySelector('#schedule-options')
  .querySelector('#id_schedule_start_datetime')
  .shadowRoot.querySelector('lit-flatpickr');
  
  datePickerElem.setAttribute('minDate', getTodaysDate());
}

function handleSubmissionResponse(requiredFields, form, responseData) {
  const { htmlString, jqXHR } = responseData;
  const responseForm = $($.parseHTML(htmlString));
  const hasErrors = responseForm.find('div.error').length > 0;
  if (hasErrors) {
    for (let i = 0; i < requiredFields.length; i += 1) {
      const fieldId = requiredFields[i];
      $(form).find(fieldId).html(responseForm.find(fieldId));
    }
  }
  
  if (!hasErrors) {
    window.document.location.href = jqXHR.getResponseHeader('REDIRECT') || '/trigger/';
  }
}

function initForm(options) {
  const {
    confirmationBoxId, flowFieldId, successFunc, postUrl, formContainerId
  } = options;
  const section =  $(formContainerId);
  const form = section.find('form');
  const confirmationBox = document.querySelector(confirmationBoxId);
  form.off('submit').on('submit', function(e) {
    e.preventDefault();
    const flowName = getFLowName(flowFieldId);
    const modalMessage = $(confirmationBoxId).find('div.p-6');
    modalMessage.text(getMessageTemplate().replace('{flowName}', flowName));
    confirmationBox.classList.remove('hide');
    confirmationBox.open = true;
  });
  
  confirmationBox.addEventListener('temba-button-clicked', function(event) {
    const container = formContainerId + ' > .formax-container';
    const options = { form, container, successFunc, postUrl };
    if (!event.detail.button.secondary) submitFormData(options);
    confirmationBox.classList.add('hide');
    confirmationBox.open = false;
  });
}
