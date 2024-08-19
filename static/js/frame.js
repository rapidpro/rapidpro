var pendingRequests = [];

function onSpload(fn) {
  var container = document.querySelector('.spa-container');
  if (!container) {
    document.addEventListener('DOMContentLoaded', fn, { once: true });
  } else {
    var isInitial = container.classList.contains('initial-load');
    var isLoading = container.classList.contains('loading');
    if (isInitial) {
      document.addEventListener('DOMContentLoaded', fn, { once: true });
    } else {
      if (isLoading) {
        var eventContainer = document.querySelector('.spa-content');
        if (eventContainer) {
          eventContainer.addEventListener('temba-spa-ready', fn, {
            once: true
          });
        }
      } else {
        window.setTimeout(fn, 0);
      }
    }
  }
}

function conditionalLoad(local, remote) {
  if (
    local != null &&
    (window.location.hostname == 'localhost' || remote == null)
  ) {
    loadResource(window.static_url + local);
  } else if (remote != null) {
    loadResource(remote);
  }
}

function loadResource(src) {
  (function () {
    document.write(unescape('%3Cscript src="' + src + '"%3E%3C/script%3E'));
  })();
}

function goto(event, ele) {
  var container = document.querySelector('.spa-container');
  if (container) {
    container.classList.remove('initial-load');
  }
  if (event.target != ele) {
    if (event.target.href) {
      event.stopPropagation();
      event.preventDefault();

      var link = event.target.href;
      if (event.metaKey) {
        window.open(link, '_blank');
      } else if (event.target.target) {
        window.open(link, event.target.target);
      } else {
        document.location.href = link;
      }
      return;
    }
  }

  if (!ele) {
    ele = event.target;
  }

  event.stopPropagation();
  event.preventDefault();

  if (ele.setActive) {
    ele.setActive();
  }
  var href = ele.getAttribute('href');

  if (!href) {
    if (ele.tagName == 'TD') {
      href = ele.closest('tr').getAttribute('href');
    }
  }

  if (href) {
    if (event.metaKey) {
      window.open(href, '_blank');
    } else {
      spaGet(href);
    }
  }
}

function addClass(selector, className) {
  document.querySelectorAll(selector).forEach(function (ele) {
    ele.classList.add(className);
  });
}

function refreshMenu() {
  var menu = document.querySelector('temba-menu');
  if (menu) {
    menu.refresh();
  }
}

function refreshGlobals() {
  var store = document.querySelector('temba-store');
  if (store) {
    store.refreshGlobals();
  }
}

function showLoading() {
  addClass('.spa-container', 'loading');
}

function hideLoading(response) {
  var container = document.querySelector('.spa-container');
  if (container) {
    container.classList.remove('loading');
  }

  // scroll our content to the top if needed
  var content = document.querySelector('.spa-content');
  if (content) {
    content.scrollTo(0, 0);
  }

  var menu = document.querySelector('temba-menu');
  if (menu && response) {
    var menuSelection = response.headers.get('temba_menu_selection');
    if (menu && menuSelection) {
      menu.setFocusedItem(menuSelection);
    }
  }

  var eventContainer = document.querySelector('.spa-content');
  if (eventContainer) {
    eventContainer.dispatchEvent(new CustomEvent('temba-spa-ready'));
  }
  refreshMenu();
}

function handleUpdateComplete() {
  // scroll to the top
  var content = document.querySelector('.spa-container');
  if (content) {
    content.scrollTo({
      top: 0,
      left: 0,
      behavior: 'smooth'
    });
  }
}

function addToHistory(url) {
  if (url.indexOf('http') == -1) {
    url = document.location.origin + url;
  }
  window.history.pushState({ url: url }, '', url);
}

function spaGet(url, triggerEvents) {
  spaRequest(url, { ignoreEvents: !triggerEvents });
}

function spaPost(url, options) {
  options = options || {};

  const requestOptions = {
    ignoreEvents: false,
    ignoreHistory: false,
    headers: options.headers || {}
  };

  if (options.queryString) {
    requestOptions.body = options.queryString;
    requestOptions.headers['Content-Type'] =
      'application/x-www-form-urlencoded';
  } else if (options.postData) {
    requestOptions.body = options.postData;
  }

  requestOptions.showErrors = options.showErrors;
  return spaRequest(url, requestOptions);
}

function spaRequest(url, options) {
  if (!checkForUnsavedChanges()) {
    return;
  }

  showLoading();

  var refererPath = window.location.pathname;

  options = options || {};
  const ignoreEvents = options.ignoreEvents || false;
  const ignoreHistory = options.ignoreHistory || false;
  const body = options.body || null;
  const headers = options.headers || {};

  headers['TEMBA-REFERER-PATH'] = refererPath;
  headers['TEMBA-PATH'] = url;

  if (!ignoreHistory) {
    addToHistory(url);
  }

  const ajaxOptions = {
    container: '.spa-content',
    headers,
    ignoreEvents: ignoreEvents,
    cancel: true,
    showErrors: !!options.showErrors
  };

  if (body) {
    ajaxOptions.method = 'POST';
    ajaxOptions.body = body;
  }

  return fetchAjax(url, ajaxOptions).then(hideLoading);
}

function fetchAjax(url, options) {
  // create our default options
  options = options || {};

  if (options['cancel']) {
    pendingRequests.forEach(function (controller) {
      controller.abort();
    });
    pendingRequests = [];
  }

  let csrf = getCookie('csrftoken');
  if (!csrf) {
    const tokenEle = document.querySelector('[name=csrfmiddlewaretoken]');
    if (tokenEle) {
      csrf = tokenEle.value;
    }
  }

  options['headers'] = options['headers'] || {};

  if (csrf) {
    options['headers']['X-CSRFToken'] = csrf;
  }

  options['headers']['TEMBA-SPA'] = 1;
  options['headers']['X-PJAX'] = 1;

  let container = options['container'] || null;

  // reroute any pjax requests made from spa pages and push the content there instead
  if (container == '#pjax' && document.querySelector('.spa-content')) {
    container = '.spa-content';
  }

  var controller = new AbortController();
  pendingRequests.push(controller);
  options['signal'] = controller.signal;
  var toFetch = url;

  return fetch(toFetch, options)
    .then(function (response) {
      const toasts = response.headers.get('x-temba-toasts');
      if (toasts) {
        const toastEle = document.querySelector('temba-toast');
        if (toastEle) {
          toastEle.addMessages(JSON.parse(toasts));
        }
      }

      // remove our controller
      pendingRequests = pendingRequests.filter(function (controller) {
        return response.controller === controller;
      });

      // if we have a version mismatch, reload the page
      var version = response.headers.get('x-temba-version');
      var org = response.headers.get('x-temba-org');

      if (response.type !== 'cors' && org && org != org_id) {
        if (response.redirected) {
          document.location.href = response.url;
        } else {
          document.location.href = toFetch;
        }
        return response;
      }

      if (version && tembaVersion != version) {
        document.location.href = toFetch;
        return response;
      }

      if (
        !options.showErrors &&
        (response.status < 200 || response.status > 299)
      ) {
        return response;
      }

      if (container) {
        // if we got redirected when updating our container, make sure reflect it in the url
        if (response.redirected) {
          if (response.url) {
            window.history.replaceState(
              { url: response.url },
              '',
              response.url
            );
          }
        }

        // special case for spa content, break out into a full page load
        if (
          container === '.spa-content' &&
          response.headers.get('x-temba-content-only') != 1
        ) {
          document.location.href = response.url;
          return;
        }

        return response.text().then(function (body) {
          var containerEle = document.querySelector(container);
          if (containerEle) {
            // special care to unmount the editor
            var editor = document.querySelector('#rp-flow-editor');
            if (editor) {
              window.unmountEditor(editor);
            }

            setInnerHTML(containerEle, body);
            var title = document.querySelector('#title-text');
            if (title) {
              document.title = title.innerText;
            }

            // wire up any posterize links in the content body
            containerEle.querySelectorAll('.posterize').forEach(function (ele) {
              ele.addEventListener('click', function () {
                handlePosterize(ele);
              });
            });
          }
          return response;
        });
      }
      return response;
    })
    .catch(function (e) {
      // canceled
    });
}

function handleMenuClicked(event) {
  var items = event.detail;

  var item = items.item;
  var selection = items.selection;

  if (item.event) {
    document.dispatchEvent(new CustomEvent(item.event, { detail: item }));
    return;
  }

  if (item.type == 'modax-button') {
    var modaxOptions = {
      disabled: false,
      onSubmit: item.on_submit
    };
    showModax(item.name, item.href, modaxOptions);
    return;
  }

  if (!item.popup && selection.length > 1 && selection[0] == 'ticket') {
    if (window.handleTicketsMenuChanged) {
      handleTicketsMenuChanged(item);
    }
  }

  // posterize if called for
  if (item.href && item.posterize) {
    posterize(item.href);
  }
}

function checkForUnsavedChanges() {
  var store = document.querySelector('temba-store');
  if (store) {
    const unsavedChanges = store.getDirtyMessage();
    if (unsavedChanges) {
      return confirm(unsavedChanges);
    }
  }
  return true;
}

function handleMenuChanged(event) {
  var selection = event.target.getSelection();
  var menuItem = event.target.getMenuItem();
  if (menuItem && menuItem.href) {
    spaGet(menuItem.href);
  }

  // TODO: refactor this to be event driven
  if (selection.length > 1) {
    var section = selection[0];
    var name = `handle${section.charAt(0).toUpperCase()}${section.slice(
      1
    )}MenuChanged`;
    if (this[name]) {
      this[name](menuItem);
    }
  }
}

function showModax(header, endpoint, modaxOptions) {
  const lastElement = document.activeElement;
  var options = modaxOptions || {};
  var modax = document.querySelector('temba-modax#shared-modax');
  if (modax) {
    modax.className = options.id || '';
    modax['-temba-loaded'] = undefined;

    modax.disabled = options.disabled == 'True';
    var itemOnSubmit;
    if (options.onSubmit == 'None') {
      onSubmit = undefined;
    }

    if (options.onSubmit) {
      modax['-temba-submitted'] = Function(options.onSubmit);
    } else {
      modax['-temba-submitted'] = undefined;
    }

    if (options.onRedirect) {
      modax['-temba-redirected'] = Function(options.onRedirect);
    } else {
      modax['-temba-redirected'] = refreshMenu;
    }

    modax.headers = { 'TEMBA-SPA': 1 };
    modax.header = header;
    modax.endpoint = endpoint;

    // take our focus from the thing that invocked us
    if (lastElement) {
      lastElement.blur();
    }
    modax.open = true;
  }
}

function handleWorkspaceChanged(orgId) {
  spaPost('/org/choose/', { queryString: 'organization=' + orgId });
}

document.addEventListener('temba-redirected', function (event) {
  spaGet(event.detail.url, true);
});

document.addEventListener('temba-pjax-complete', function () {
  refreshMenu();
  hideLoading();
  handleUpdateComplete();
});

function loadFromState(state) {
  if (state && state.url) {
    var url = state.url;
    spaRequest(url, { ignoreEvents: false, ignoreHistory: true });
  }
}

function reloadContent() {
  const store = document.querySelector('temba-store');
  store.clearCache();
  loadFromState(history.state);
}

window.addEventListener('popstate', function (event) {
  loadFromState(event.state);
});

document.addEventListener('DOMContentLoaded', function () {
  var content = document.querySelector('.spa-content');
  if (content) {
    content.addEventListener('submit', function (evt) {
      var formEle = evt.target;
      if (formEle.closest('.formax-section')) {
        return;
      }

      var url = formEle.action || document.location.href;

      if (formEle.method.toLowerCase() !== 'post') {
        evt.stopPropagation();
        evt.preventDefault();
        var formData = new FormData(formEle);
        let queryString = new URLSearchParams(formData).toString();
        if (queryString) {
          if (url.indexOf('?') > 0) {
            url += '&' + queryString;
          } else {
            url += '?' + queryString;
          }
        }
        spaGet(url);
      } else {
        evt.stopPropagation();
        evt.preventDefault();

        if (url.indexOf('/org/service') > -1) {
          formEle.submit();
        } else {
          spaPost(url, { postData: new FormData(formEle) });
        }
      }
    });
  }
});

function posterize(href) {
  var url = new URL(href, window.location.origin);
  spaPost(url.pathname, { queryString: url.searchParams });
}

function handlePosterize(ele) {
  posterize(ele.getAttribute('href') || ele.dataset.href);
}

function handlePosterizeClick(event) {
  event.preventDefault();
  event.stopPropagation();
  handlePosterize(event.currentTarget);
}

function removalConfirmation(removal, buttonName) {
  var modal = document.querySelector('#general-delete-confirmation');
  if (modal) {
    modal.classList.remove('hidden');

    // set modal deets
    var title = document.querySelector('.' + removal + ' > .title').innerHTML;
    var body = document.querySelector('.' + removal + ' > .body').innerHTML;

    modal.header = title;
    modal.querySelector('.confirmation-body').innerHTML = body;

    modal.open = true;

    modal.addEventListener('temba-button-clicked', function (event) {
      if (!event.detail.button.secondary) {
        var ele = document.querySelector('#' + removal + '-form');
        handlePosterize(ele);
      }
      modal.open = false;

      // clear our listeners
      modal.outerHTML = modal.outerHTML;
    });
  }
}

function formatContact(item) {
  if (item.text.indexOf(' (') > -1) {
    var name = item.text.split('(')[0];
    if (name.indexOf(')') == name.length - 1) {
      name = name.substring(0, name.length - 1);
    }
    return name;
  }
  return item.text;
}

function handleNewWorkspaceClicked(evt) {
  var modal = getModax();
  modal.header = 'New Workspace';
  modal.setAttribute('endpoint', '/org/create');
  modal.open = true;

  evt.preventDefault();
  evt.stopPropagation();
}

document.addEventListener('DOMContentLoaded', function () {
  // remove our initial load marker
  var container = document.querySelector('.spa-container');
  if (container) {
    container.classList.remove('initial-load');
  }

  container.addEventListener('click', function (event) {
    // get our immediate path
    const path = event.composedPath().slice(0, 10);

    // find the first anchor tag
    const ele = path.find((ele) => ele.tagName === 'A');

    if (ele) {
      const url = new URL(ele.href);
      event.preventDefault();
      event.stopPropagation();

      // if we are working within the app, use spaGet
      if (url.host === window.location.host && !event.metaKey) {
        spaGet(ele.href);
      } else {
        // otherwise open a new tab
        window.open(ele.href, '_blank');
      }
    }
  });
});
