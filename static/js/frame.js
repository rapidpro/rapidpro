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
      container.classList.remove('initial-load');
    } else {
      if (isLoading) {
        var eventContainer = document.querySelector('.spa-content');
        if (eventContainer) {
          eventContainer.addEventListener('temba-spa-ready', fn, {
            once: true,
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

function fetchAjax(url, container, options) {
  if (options['cancel']) {
    pendingRequests.forEach(function (controller) {
      controller.abort();
    });
    pendingRequests = [];
  }

  options = options || {};

  // reroute any pjax requests made from spa pages and push the content there instead
  if (container == '#pjax' && document.querySelector('.spa-content')) {
    container = '.spa-content';
    options['headers'] = options['headers'] || {};
    options['headers']['TEMBA-SPA'] = 1;
    options['headers']['X-PJAX'] = 1;
  }

  var controller = new AbortController();
  pendingRequests.push(controller);
  options['signal'] = controller.signal;
  var toFetch = url;
  fetch(toFetch, options)
    .then(function (response) {
      // remove our controller
      pendingRequests = pendingRequests.filter(function (controller) {
        return response.controller === controller;
      });

      // if we have a version mismatch, reload the page
      var version = response.headers.get('x-temba-version');
      if (tembaVersion != version) {
        document.location.href = toFetch;
        return;
      }

      if (org_id != response.headers.get('x-temba-org')) {
        document.location.href = toFetch;
        return;
      }

      if (response.status < 200 || response.status > 299) {
        return;
      }

      if (response.redirected) {
        var url = response.url;
        window.history.replaceState({ url: url }, '', url);
      }

      if (response.headers.get('x-temba-content-only') != 1) {
        document.location.href = url;
        return;
      }

      response.text().then(function (body) {
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

          if (options) {
            if ('onSuccess' in options) {
              options['onSuccess'](response);
            }
          }
        }
      });
    })
    .catch(function (e) {
      // canceled
    });
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
      fetchURL(href);
    }
  }
}

function addClass(selector, className) {
  document.querySelectorAll(selector).forEach(function (ele) {
    ele.classList.add(className);
  });
}

function showLoading(full) {
  if (full) {
    addClass('.widget-container', 'loading');
  } else {
    addClass('.spa-container', 'loading');
  }
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

function hideLoading(response) {
  var containers = document.querySelectorAll(
    '.spa-container, .widget-container'
  );
  for (cont of containers) {
    cont.classList.remove('loading');
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
      behavior: 'smooth',
    });
  }
}

function addToHistory(url) {
  if (url.indexOf('http') == -1) {
    url = document.location.origin + url;
  }
  window.history.pushState({ url: url }, '', url);
}

function gotoURL(url, ignoreEvents, ignoreHistory) {
  var refererPath = window.location.pathname;

  if (!ignoreHistory) {
    addToHistory(url);
  }

  fetchAjax(url, '.spa-content', {
    headers: {
      'TEMBA-SPA': '1',
      'TEMBA-REFERER-PATH': refererPath,
      'TEMBA-PATH': url,
    },
    onSuccess: hideLoading,
    ignoreEvents: ignoreEvents,
    cancel: true,
  });
}

function fetchURL(url, triggerEvents) {
  showLoading();
  gotoURL(url, !triggerEvents);
}

function handleMenuClicked(event) {
  var items = event.detail;

  var item = items.item;
  var parent = items.parent;
  var selection = items.selection;

  if (item.trigger) {
    if (item.href) {
      window.open(item.href, '_blank');
    }
    return;
  }

  if (item.type == 'modax-button') {
    var modaxOptions = {
      disabled: false,
      onSubmit: item.on_submit,
    };
    showModax(item.name, item.href, modaxOptions);
  }

  if (!item.popup && selection.length > 1 && selection[0] == 'ticket') {
    if (window.handleTicketsMenuChanged) {
      handleTicketsMenuChanged(item);
    }
  }

  // clicked inside our workspace popup
  if (parent && parent.id == 'workspace') {
    if (item.id == 'settings') {
      fetchURL('/org/workspace');
      var menu = document.querySelector('temba-menu');
      if (menu) {
        menu.click();
      }
    } else if (item.posterize) {
      posterize(item.href);
    } else {
      handleWorkspaceChanged(item.id);
    }
  }
}

function handleMenuChanged(event) {
  var selection = event.target.getSelection();
  var menuItem = event.target.getMenuItem();
  if (menuItem && menuItem.href) {
    showLoading();
    gotoURL(menuItem.href);
  }

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
    modax.open = true;
  }
}

function handleWorkspaceChanged(orgId) {
  showLoading(true);
  var store = document.querySelector('temba-store');
  store
    .postUrl(
      '/org/choose/',
      'organization=' + orgId,
      {},
      'application/x-www-form-urlencoded'
    )
    .then(function (response) {
      if (response.redirected) {
        document.location.href = response.url;
      }
    });
}

document.addEventListener('temba-redirected', function (event) {
  fetchURL(event.detail.url, true);
});

document.addEventListener('temba-pjax-complete', function () {
  refreshMenu();
  hideLoading();
  handleUpdateComplete();
});

function loadFromState(state) {
  if (state && state.url) {
    showLoading();

    var url = state.url;
    gotoURL(url, false, true);
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
        showLoading();

        if (queryString) {
          if (url.indexOf('?') > 0) {
            url += '&' + queryString;
          } else {
            url += '?' + queryString;
          }
        }

        gotoURL(url);
      } else {
        evt.stopPropagation();
        evt.preventDefault();

        if (url.indexOf('/org/service') > -1) {
          formEle.submit();
        } else {
          var formData = new FormData(formEle);
          showLoading();

          var store = document.querySelector('temba-store');
          if (store) {
            store
              .postUrl(url, formData, { 'TEMBA-SPA': '1' })
              .then(function (response) {
                var content = document.querySelector('.spa-content');

                // remove jquery use here
                $(content).html(response.body);

                if (response.redirected) {
                  addToHistory(response.url);
                }

                hideLoading(response);
              });
          }
        }
      }
    });
  }
});

function fetchPJAXContent(url, container, options) {
  options = options || {};

  // hijack any pjax requests made from spa pages and route the content there instead
  if (container == '#pjax' && document.querySelector('.spa-content')) {
    container = '.spa-content';
    options['headers'] = options['headers'] || {};
    options['headers']['TEMBA-SPA'] = 1;
  }

  var triggerEvents = true;
  if (!!options['ignoreEvents']) {
    triggerEvents = false;
  }

  var type = 'GET';
  var data = undefined;
  var processData = true;
  var contentType = 'application/x-www-form-urlencoded; charset=UTF-8';

  if (options) {
    if ('postData' in options) {
      type = 'POST';
      data = options['postData'];
    }

    if ('formData' in options) {
      type = 'POST';
      processData = false;
      data = options['formData'];
      contentType = false;
    }
  }

  var headers = { 'X-PJAX': true };
  if (options && 'headers' in options) {
    for (key in options['headers']) {
      headers[key] = options['headers'][key];
    }
  }

  if (triggerEvents) {
    document.dispatchEvent(new Event('temba-pjax-begin'));
  }

  // see if we should skip our fetch
  if (options) {
    if ('shouldIgnore' in options && options['shouldIgnore']()) {
      if ('onIgnore' in options) {
        options['onIgnore']();
      }
      return;
    }
  }

  var request = {
    headers: headers,
    type: type,
    url: url,
    contentType: contentType,
    processData: processData,
    data: data,
    success: function (response, status, jqXHR) {
      if ('followRedirects' in options && options['followRedirects'] == true) {
        var redirect = jqXHR.getResponseHeader('REDIRECT');
        if (redirect) {
          window.document.location.href = redirect;
          return;
        }
      }

      // double check before replacing content
      if (options) {
        if ('shouldIgnore' in options && options['shouldIgnore'](response)) {
          if ('onIgnore' in options) {
            options['onIgnore'](jqXHR);
          }

          return;
        }
      }

      $(container).html(response);

      if (triggerEvents) {
        document.dispatchEvent(new Event('temba-pjax-complete'));
      }

      if (options) {
        if ('onSuccess' in options) {
          options['onSuccess']();
        }
      }
    },
  };
  $.ajax(request);
}

function posterize(href) {
  var url = $.url(href);
  $('#posterizer').attr('action', url.attr('path'));
  for (var key in url.param()) {
    $('#posterizer').append(
      "<input type='hidden' name='" +
        key +
        "' value='" +
        url.param(key) +
        "'></input>"
    );
  }
  $('#posterizer').submit();
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

function createContactChoice(term, data) {
  if (
    $(data).filter(function () {
      return this.text.localeCompare(term) === 0;
    }).length === 0
  ) {
    if (!isNaN(parseFloat(term)) && isFinite(term)) {
      return { id: 'number-' + term, text: term };
    }
  }
}

function handleNewWorkspaceClicked(evt) {
  var modal = getModax();
  modal.header = 'New Workspace';
  modal.setAttribute('endpoint', '/org/create');
  modal.open = true;

  evt.preventDefault();
  evt.stopPropagation();
}

onSpload(function () {
  document.querySelectorAll('.spa-content .posterize').forEach(function (ele) {
    ele.addEventListener('click', function () {
      handlePosterize(ele);
    });
  });
});
