// handle lack of console on IE
if (typeof console == 'undefined') {
  this.console = { log: function (msg) {} };
}

function downloadFile(evt, url) {
  evt.stopPropagation();
  evt.preventDefault();
  window.open(url, '_download');
}

function openWindow(evt, url, target) {
  evt.stopPropagation();
  evt.preventDefault();
  window.open(url, target);
}

function showLightbox(evt, url) {
  evt.stopPropagation();
  evt.preventDefault();
  const lightbox = document.querySelector('temba-lightbox');
  if (lightbox.zoom) {
    lightbox.zoom = false;
  } else {
    lightbox.showElement(evt.target);
  }
}

function showPreview(evt, ele) {
  evt.stopPropagation();
  evt.preventDefault();

  var dialog = document.querySelector('#shared-dialog');
  dialog.width = 'initial';
  dialog.buttons = [{ type: 'secondary', name: 'Ok', closes: true }];

  var container = document.createElement('div');
  container.style = 'text-align:center;line-height:0px;padding:0px';
  container.innerHTML = ele.getAttribute('attachment');
  dialog.body = container;
  dialog.open = true;
}

function getModax(id) {
  var modax = document.querySelector(id);
  if (!modax) {
    modax = document.querySelector('#shared-modax');
  }
  return modax;
}

function checkInner(event) {
  if (event.target) {
    var checkbox = event.target.querySelector('temba-checkbox');
    if (checkbox) {
      checkbox.click();
      event.preventDefault();
      event.stopPropagation();
    }
  }
}

function gotoLink(href) {
  document.location.href = href;
}

function setCookie(name, value, path) {
  if (!path) {
    path = '/';
  }
  var now = new Date();
  now.setTime(now.getTime() + 60 * 1000 * 60 * 24 * 30);
  document.cookie = `${name}=${value};expires=${now.toUTCString()};path=${path}`;
}

function getCookie(name) {
  var cookieValue = null;
  if (document.cookie && document.cookie != '') {
    var cookies = document.cookie.split(';');
    for (var i = 0; i < cookies.length; i++) {
      var cookie = cookies[i].trim();
      if (cookie.substring(0, name.length + 1) === name + '=') {
        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
        break;
      }
    }
  }
  return cookieValue;
}

function csrfSafeMethod(method) {
  // these HTTP methods do not require CSRF protection
  return /^(GET|HEAD|OPTIONS|TRACE)$/.test(method);
}

// no operation here we'll overwrite this when needed
function update_schedule() {}

function intersect(a, b) {
  var ai = 0,
    bi = 0;
  var result = new Array();

  while (ai < a.length && bi < b.length) {
    if (a[ai] < b[bi]) {
      ai++;
    } else if (a[ai] > b[bi]) {
      bi++;
    } else {
      result.push(a[ai]);
      ai++;
      bi++;
    }
  }
  return result;
}

function numericComparator(a, b) {
  return a - b;
}

/**
 * We use video.js to provide a more consistent experience across different browsers
 * @param element the <video> element
 */
function initializeVideoPlayer(element) {
  videojs(element, {
    plugins: {
      vjsdownload: {
        beforeElement: 'playbackRateMenuButton',
        textControl: 'Download',
        name: 'downloadButton',
      },
    },
  });
}

function disposeVideoPlayer(element) {
  var player = videojs.getPlayers()[element.playerId];
  if (player) {
    player.dispose();
  }
}

function wireTableListeners() {
  var tds = document.querySelectorAll('table.selectable tr td:not(.checkbox)');

  for (var td of tds) {
    td.addEventListener('mouseenter', function () {
      var tr = this.parentElement;
      tr.classList.add('hovered');
    });

    td.addEventListener('mouseleave', function () {
      var tr = this.parentElement;
      tr.classList.remove('hovered');
    });

    td.addEventListener('click', function () {
      var tr = this.parentElement;
      eval(tr.getAttribute('onrowclick'));
    });
  }
}

function stopEvent(event) {
  event.stopPropagation();
  event.preventDefault();
}


document.addEventListener('temba-refresh-complete', function () {
  wireTableListeners();
});

// wire up our toggle tables
document.addEventListener('DOMContentLoaded', function () {
  wireTableListeners();
  document
    .querySelectorAll('table.list.toggle > thead')
    .forEach(function (ele) {
      var table = ele.parentElement;
      var classes = table.classList;
      var stateful = classes.contains('stateful');

      // read in our cookie if we are stateful
      if (stateful) {
        if (getCookie('rp-table-expanded-' + table.id) == 'true') {
          classes.add('expanded');
        }
      }

      ele.addEventListener('click', function () {
        classes.toggle('expanded');

        // set a cookie
        if (stateful) {
          setCookie(
            'rp-table-expanded-' + table.id,
            classes.contains('expanded')
          );
        }
      });
    });
});

var setInnerHTML = function (ele, html) {
  var scripts = ele.parentNode.querySelectorAll('script');
  scripts.forEach(function (script) {
    script.parentNode.removeChild(script);
  });

  ele.innerHTML = html;

  Array.from(ele.querySelectorAll('script')).forEach(function (oldScript) {
    oldScript.parentNode.removeChild(oldScript);

    var newScript = document.createElement('script');
    Array.from(oldScript.attributes).forEach(function (attr) {
      newScript.setAttribute(attr.name, attr.value);
    });
    newScript.appendChild(document.createTextNode(oldScript.innerHTML));
    ele.parentNode.appendChild(newScript);
  });
};
