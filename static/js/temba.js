// handle lack of console on IE
if (typeof console == 'undefined') {
    this.console = { log: function (msg) {} };
}

function goto(event, ele) {
    if (!ele) {
        ele = event.target;
    }

    event.stopPropagation();
    if (ele.setActive) {
        ele.setActive();
    }
    var href = ele.getAttribute('href');
    if (href) {
        if (event.metaKey) {
            window.open(href, '_blank');
        } else {
            document.location.href = href;
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
            var cookie = jQuery.trim(cookies[i]);
            // Does this cookie string begin with the name we want?
            if (cookie.substring(0, name.length + 1) == name + '=') {
                cookieValue = decodeURIComponent(
                    cookie.substring(name.length + 1)
                );
                break;
            }
        }
    }
    return cookieValue;
}
var csrftoken = getCookie('csrftoken');

function csrfSafeMethod(method) {
    // these HTTP methods do not require CSRF protection
    return /^(GET|HEAD|OPTIONS|TRACE)$/.test(method);
}
$.ajaxSetup({
    crossDomain: false, // obviates need for sameOrigin test
    beforeSend: function (xhr, settings) {
        if (!csrfSafeMethod(settings.type)) {
            xhr.setRequestHeader('X-CSRFToken', csrftoken);
        }
    },
});

$(document).ready(function () {
    $('iframe').each(function () {
        /*fix youtube z-index*/
        var url = $(this).attr('src');
        if (url.indexOf('youtube.com') >= 0) {
            if (url.indexOf('?') >= 0) {
                $(this).attr('src', url + '&wmode=transparent');
            } else {
                $(this).attr('src', url + '?wmode=transparent');
            }
        }
    });

    $('ul.nav li.dropdown').hover(
        function () {
            $(this).find('.dropdown-menu').stop(true, true).delay(200).fadeIn();
        },
        function () {
            $(this)
                .find('.dropdown-menu')
                .stop(true, true)
                .delay(200)
                .fadeOut();
        }
    );

    $('.pollrun-select-btn').on('click', pollRunSelectHandle);
});

function pollRunSelect(pollRunId) {
    $('input#pollrun').val(pollRunId);
    $('form[name=pollrun]').submit();
}

function pollRunSelectHandle() {
    pollRunSelect($(this).data('id'));
    $('#pollrun-text > span.text').text($(this).text());
}

function getCheckedIds() {
    return Array();
}

bindRefreshBlock();
var dropDownOpen = false;

function bindRefreshBlock() {
    $('[data-toggle=dropdown]').on('focus', function () {
        dropDownOpen = true;
        hideTooltip();
    });

    $('[data-toggle=dropdown]').on('blur', function () {
        // defer to if we have checked items to block refresh
        dropDownOpen = false;
        hideTooltip();
    });
}

/**
 * Listen for thes start of pjax refreshes and block them if appropriate
 */
document.addEventListener('temba-refresh-begin', function () {
    var checkedIds = getCheckedIds().length > 0;
    let openedModals = false;
    var modals = document.querySelectorAll('temba-modax');
    var activeElement = document.activeElement.tagName;
    var openMenu = !!document.querySelector('.gear-menu.open');
    var selection = !!window.getSelection().toString();

    var focused = activeElement == 'TEMBA-TEXTINPUT';

    for (var modal of modals) {
        if (modal.open) {
            openedModals = true;
            break;
        }
    }

    var pjaxElement = document.querySelector('#pjax');

    if (pjaxElement) {
        pjaxElement.setAttribute(
            'data-no-pjax',
            dropDownOpen ||
                checkedIds ||
                openedModals ||
                focused ||
                openMenu ||
                selection
        );
    }
});

function getStartTime() {
    if ($('#later-option').attr('checked')) {
        return moment(new Date($('#start-datetime-value').val() * 1000));
    } else {
        return moment();
    }
}

function getStartHour() {
    var time = getStartTime();
    var hour = time.getHours();
    if (hour > 12) {
        hour = hour - 12 + 'pm';
    } else {
        hour += 'am';
    }
    return hour;
}

// no operation here we'll overwrite this when needed
function update_schedule() {}

function updateDailySelection() {
    var selected = 0;
    $('.btn-group > .btn').each(function () {
        if ($(this).hasClass('active')) {
            selected += parseInt($(this).attr('value'));
        }
    });
    $('#repeat-days-value').val(selected);
}

function scheduleSelection(event) {
    // prevent default bootstrap behavior
    event.stopPropagation();

    // togger our active class
    if ($(this).attr('data-toggle') != 'button') {
        $(this).toggleClass('active');
    }

    // make sure at least one stays selected
    var selected = $('.btn-group > .btn.active').length;
    if (selected == 0 && !$(this).hasClass('active')) {
        $(this).toggleClass('active');
    }

    updateDailySelection();
}

function hideTooltip() {
    $('.tooltip').fadeOut();
}

function updateFile() {
    var file = $('#csv_file').val();

    while (file.indexOf('\\') >= 0) {
        file = file.substring(file.indexOf('\\') + 1, file.length);
    }
    $('#file-field').val(file);
}

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

function messageTextareaLengthCheck() {
    var length = $(this).val().length;
    var messages = Math.ceil(length / 160);
    var left = messages * 160 - length;

    if (messages < 2) {
        $('#counter').text('' + left);
    } else {
        $('#counter').text('' + left + ' / ' + messages);
    }
}

function initMessageLengthCounter(textarea, counter) {
    function onKeyUp() {
        var ta = $(textarea);
        if (ta) {
            var val = ta.val();
            var length = 0;
            if (val) {
                length = val.length;
            }

            var messages = Math.ceil(length / 160);
            var left = messages * 160 - length;

            if (length == 0) {
                $(counter).text('' + 160);
            } else if (messages < 2) {
                $(counter).text('' + left);
            } else {
                $(counter).text('' + left + ' / ' + messages);
            }
        }
    }

    $(textarea).live('keyup', onKeyUp);

    // set our initial length
    onKeyUp();
}

function toggle_section() {
    var shrink;
    $('.form-section').each(function () {
        var visible = $(this);
        if (visible.find('.expanded').is(':visible')) {
            hide_section(visible);
            shrink = visible;
        }
    });

    var row = $(this).parent('.form-section');

    if (!shrink || (shrink && row.attr('id') != shrink.attr('id'))) {
        var expanded = row.find('.expanded');
        if (expanded.is(':visible')) {
            hide_section(row);
        } else {
            expand_section(row);
        }
    }
}

function hide_section(section) {
    if (!section.hasClass('error')) {
        section.addClass('expandable');
    }

    try {
        eval('update_' + section.attr('id') + '()');
    } catch (e) {}

    section
        .find('.section-icon')
        .animate(
            { 'font-size': '35px', width: '40px', height: '40px' },
            200,
            function () {
                // section.removeClass('expanded');
            }
        );
    section.find('.expanded').hide();
    section.find('.summary').fadeIn('slow');
}

function expand_section(section) {
    section.removeClass('expandable');
    section
        .find('.section-icon')
        .animate(
            { 'font-size': '80px', width: '100px', height: '100px' },
            200,
            function () {
                // section.addClass('expanded');
            }
        );
    section.find('.expanded').slideDown('fast');
    section.find('.summary').hide();
}

//function initializeDatetimePicker(){var nextStart=new Date();var minutes=nextStart.getMinutes();if(minutes>0){nextStart.setMinutes(0);nextStart.setHours(nextStart.getHours()+1);}
//    var dateFormat="DD, MM d, yy";var timeFormat="h:mm tt";var initial=$.datepicker.formatDate(dateFormat,nextStart)+" at "+$.datepicker.formatTime(timeFormat,nextStart);setDatetimeValue(initial,null,nextStart);$('#start-datetime').datetimepicker({dateFormat:"DD, MM d, yy",showMinute:false,showButtonPanel:true,pickerTimeFormat:"'Start at' "+timeFormat,timeFormat:timeFormat,separator:" at ",minDateTime:nextStart,defaultValue:initial,onSelect:setDatetimeValue});}

function setDatetimeValue(datetimeText, datepickerInstance, nextStart) {
    var datetime = null;
    if (nextStart) {
        datetime = nextStart;
    } else {
        datetime = new Date(datetimeText.replace(' at', ''));
    }
    var seconds = parseInt(datetime.getTime() / 1000);
    $('#start-datetime-value').val(seconds);
    update_schedule();
}

function resetStartDatetime() {
    var datetime = $('#start-datetime');
    if (datetime.val() == 'Later') {
        datetime.val('');
    }
    datetime.focus();
}

function startDatetimeClick() {
    $('#later-option').click();
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
    var tds = document.querySelectorAll(
        'table.selectable tr td:not(.checkbox)'
    );

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
