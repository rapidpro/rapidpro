$(document).ready(function () {
    $('iframe').each(function () {/*fix youtube z-index*/
        var url = $(this).attr("src");
        if (url.indexOf("youtube.com") >= 0) {
            if (url.indexOf("?") >= 0) {
                $(this).attr("src", url + "&wmode=transparent");
            } else {
                $(this).attr("src", url + "?wmode=transparent");
            }
        }
    });

    $('ul.nav li.dropdown').hover(function () {
        $(this).find('.dropdown-menu').stop(true, true).delay(200).fadeIn();
    }, function () {
        $(this).find('.dropdown-menu').stop(true, true).delay(200).fadeOut();
    });

    $(".pollrun-select-btn").on('click', pollRunSelectHandle);
});

function pollRunSelect(pollRunId) {
    $("input#pollrun").val(pollRunId);
    $("form[name=pollrun]").submit();
}

function pollRunSelectHandle() {
    pollRunSelect($(this).data('id'));      
    $("#pollrun-text > span.text").text($(this).text());
}

