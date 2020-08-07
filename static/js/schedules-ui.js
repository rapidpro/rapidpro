function initializeDatetimePicker(
    ele,
    minDate,
    initialDate,
    showButtons,
    user_tz,
    user_tz_offset
) {
    // force us into jQuery
    ele = $(ele);

    initialDate = initialDate || minDate;
    var hasInitial = !!initialDate;

    var initial = moment(initialDate).tz(user_tz);
    setDatetimeValue(initial, null, initialDate);

    var initialHour = null;
    var initialMinute = null;

    if (hasInitial) {
        // use our timezone shifted values
        initialHour = initial.hour();
        initialMinute = initial.minute();
        initialDate = initial.toDate();
        ele.val(initial.format('dddd, MMMM D, YYYY [at] h:mm a'));
    }

    var timeFormat = 'h:mm tt';
    ele.datetimepicker({
        dateFormat: 'DD, MM d, yy',
        timeFormat: timeFormat,
        pickerTimeFormat: "'Start at' " + timeFormat,
        separator: ' at ',
        showMinute: true,
        showButtonPanel: showButtons,
        minDateTime: null,
        defaultDate: initialDate,
        minute: initialMinute,
        hour: initialHour,
        timezone: user_tz_offset,
        onSelect: setDatetimeValue,
    });
}

function setDatetimeValue(datetimeText, datepickerInstance, nextStart) {
    var datetime = null;
    if (nextStart) {
        datetime = nextStart;
    } else {
        datetime = moment
            .tz(datetimeText, 'dddd, MMMM D, YYYY [at] h:mm a', user_tz)
            .toDate();
    }
    var seconds = parseInt(datetime.getTime() / 1000);
    $('#start-datetime-value').val(seconds);
}

// show our day selectors when we are set to weekly
$('#modal, #id-schedule, #id-trigger-schedule')
    .on('change', 'temba-select[name=repeat_period]', function () {
        var value = this.values[0].value;
        if (value == 'W') {
            if ($('.btn-group').children('.active').length == 0) {
                // account for Sunday being 0 in JS but 7 in python
                var day = getStartTime().day();
                if (day == 0) {
                    day = 7;
                }
                $('.btn-group > .btn:nth-child(' + day + ')').each(function () {
                    $(this).addClass('active');
                });
            }

            $('.weekly-repeat-options').slideDown();
            $('.weekly-repeat-options').removeClass('hide');
            updateDailySelection();
        } else {
            $('.weekly-repeat-options').slideUp();
        }
    })
    .trigger('change');

$('#modal, #id-schedule, #id-trigger-schedule').on(
    'click',
    '.weekly-repeat-options .btn-group .btn',
    scheduleSelection
);

function scheduleSelection(event) {
    // prevent default bootstrap behavior
    event.stopPropagation();

    // toggle our active class
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

function resetStartDatetime() {
    $('.start-button')
        .val('Schedule Poll')
        .removeClass('btn-success')
        .addClass('btn-primary');

    $('#schedule-next-run').show();
    $('#next-fire').hide();
    $('#start-datetime').show();
    var datetime = $('#start-datetime');
    if (
        datetime.val() == 'Schedule for later' ||
        date.val() == 'Select a date'
    ) {
        datetime.val('');
        $('#schedule-next-run').show();
    }

    datetime.focus();
    setDatetimeValue(datetime.val());

    $('#start-datetime').datetimepicker({
        showButtonPanel: false,
        timezone: user_tz_offset,
        minDateTime: null,
    });
}

function updateDailySelection() {
    var selected = '';
    $('.btn-group > .btn').each(function () {
        if ($(this).hasClass('active')) {
            selected += $(this).attr('value');
        }
    });
    $('#repeat-days-value').val(selected);
}

// handle our toggle for when to start
$(document).ready(function () {
    $('input[name=start]').on('click', function () {
        var id = $(this).attr('id');
        var actionButton = $('.start-button');
        var recurrence = $('#recurrence');

        if (id == 'later-option') {
            actionButton
                .val('Schedule')
                .addClass('btn-primary')
                .removeClass('btn-success btn-danger');
            recurrence.slideDown();
            var datetime = $('#start-datetime');
            datetime.attr('disabled', false);
            if (datetime.val() == 'Schedule for later') {
                datetime.val('');
                $('#schedule-next-run').show();
            }
            datetime.focus();
            datetime.attr('disabled', true);
        } else if (id == 'stop-option' && !$(this).hasClass('unchanged')) {
            actionButton
                .val('Cancel Schedule')
                .addClass('btn-danger')
                .removeClass('btn-primary btn-success');
            recurrence.slideUp();

            $('select[name=repeat_period]').val('O');
            $('.weekly-repeat-options').hide();
        } else {
            actionButton
                .val('Done')
                .removeClass('btn-success btn-primary btn-danger');
            recurrence.slideUp();
        }
    });
});
