var showSection = function (section) {
  if (section.dataset.readonly) {
    return;
  }
  if (section.dataset.action !== 'fixed') {
    section.classList.add('open');
  }
};

var hideSection = function (section) {
  if (section.dataset.action === 'fixed') {
    return;
  }

  section.classList.remove('open');
  return true;
};

var handleSectionClicked = function (event) {
  let section = event.target.closest('.formax-section');

  if (section.dataset.action === 'fixed') {
    return;
  }

  event.preventDefault();
  event.stopPropagation();

  const isOpen = section.classList.contains('open');

  document.querySelectorAll('.formax > .formax-section').forEach((element) => {
    hideSection(element);
  });

  if (isOpen) {
    return hideSection(section);
  } else {
    return showSection(section);
  }
};

window.fetchData = function (section) {
  var url;

  const headers = {
    'X-FORMAX': true,
    'X-PJAX': true,
    'X-FORMAX-ACTION': section.dataset.action,
  };

  if (section.closest('.spa-container')) {
    headers['TEMBA-SPA'] = 1;
  }

  if (section.dataset.href) {
    url = section.dataset.href;

    const id = '#' + section.id + ' > .formax-container';
    const options = {
      headers: headers,
      method: 'GET',
      container: id,
    };

    return fetchAjax(url, options).then(function () {
      section.dataset.loaded = true;
      _initializeForm(section);
      if (section.dataset.fixed) {
        showSection(section);
      }
      document.dispatchEvent(
        new CustomEvent('temba-formax-ready', { bubbles: true })
      );
      return section.classList.remove('hide');
    });
  } else {
    return (section.dataset.loaded = true);
  }
};

var _initializeForm = function (section) {
  var action, buttonName, form, onLoad;

  action = section.dataset.action;
  form = section.querySelector('form');
  if (action === 'formax' || action === 'redirect' || action === 'open') {
    buttonName = section.dataset.button;
    if (!buttonName) {
      buttonName = gettext('Save');
    }

    form.addEventListener('submit', _submitFormax);
    if (!section.dataset.nobutton) {
      form.append(
        '<input type="submit" class="button-primary" value="' +
          buttonName +
          '"/>'
      );
      form.querySelector('.form-actions').remove();
    }
    const submitButton = form.querySelector('.submit-button');
    if (submitButton) {
      submitButton.addEventListener('click', function () {
        this.attributes['enabled'] = false;
        this.classList.add('disabled');
      });
    }
    onLoad = section.dataset.onload;
    if (onLoad) {
      eval_(onLoad)();
    }

    if (action === 'open') {
      showSection(section);
      window.scrollTo(0, section.offset().top);
    }
  }
  if (action === 'fixed') {
    return form.attr('action', section.dataset.href);
  }
};

var _submitFormax = function (e) {
  e.preventDefault();
  const form = this;
  const section = form.closest('.formax-section');
  const followRedirects = section.dataset.action === 'redirect';

  const headers = {
    'X-FORMAX': true,
    'X-PJAX': true,
    'X-FORMAX-ACTION': section.dataset.action,
  };

  if (section.closest('.spa-container')) {
    headers['TEMBA-SPA'] = 1;
  }

  var formData = new FormData(form);
  const id = '#' + section.id + ' > .formax-container';
  const options = {
    headers: headers,
    method: 'POST',
    body: formData,
    container: id,
  };

  if (followRedirects) {
    options.redirect = 'follow';
  }

  fetchAjax(section.dataset.href, options)
    .then(function (resp) {
      const redirect = resp.headers.get('REDIRECT');
      if (redirect) {
        if (section.dataset.action === 'redirect') {
          return spaGet(redirect);
        } else {
          hideSection(section);
          fetchData(section);
        }
      } else {
        _initializeForm(section);
        var formax_form = section.querySelector('.formax-form');
        if (formax_form.classList.contains('errors')) {
          section.querySelector('.formax-summary').classList.add('hide');
          formax_form.classList.remove('hide');
        } else {
          if (section.dataset.action !== 'fixed') {
            hideSection(section);
          }
        }
        document.dispatchEvent(
          new CustomEvent('temba-formax-ready', { bubbles: true })
        );
      }
    })
    .then(function () {
      refreshMenu();
    });
};

onSpload(function () {
  document.querySelectorAll('.formax .formax-section').forEach(function (ele) {
    return _initializeForm(ele);
  });
});
