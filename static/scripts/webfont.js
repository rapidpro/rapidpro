function LoadFont(fontFamily) {
    if (fontFamily != '')
        try { WebFont.load({ google: { families: [fontFamily]} }) } catch (e) { };
}

function EmbedFont(id) {
    var arrSysFonts = ["impact", "palatino linotype", "tahoma",
        "century gothic", "lucida sans unicode", "times new roman",
        "arial narrow", "verdana", "copperplate gothic light",
        "lucida console", "gill sans mt", "trebuchet ms", "courier new",
        "arial", "georgia", "garamond",
        "arial black", "bookman old style", "courier", "helvetica"];

    var sHTML;
    if (!id) {
        sHTML = document.documentElement.innerHTML;
    } else {
        sHTML = document.getElementById(id).innerHTML;
    }
    var urlRegex = /font-family?:.+?(\;|,|")/g;
    var matches = sHTML.match(urlRegex);
    if (matches)
        for (var i = 0, len = matches.length; i < len; i++) {
            var sFont = matches[i].replace(/font-family?:/g, '').replace(/;/g, '').replace(/,/g, '').replace(/"/g, '');
            sFont = jQuery.trim(sFont);

            sFont = sFont.replace("'", "").replace("'", "");
            if ($.inArray(sFont.toLowerCase(), arrSysFonts) == -1) {
                LoadFont(sFont);
            }

            /*if (sFont != 'serif' && sFont != 'Arial' && sFont != 'Arial Black' && sFont != 'Bookman Old Style' && sFont != 'Comic Sans MS' && sFont != 'Courier' && sFont != 'Courier New' && sFont != 'Garamond' && sFont != 'Georgia' && sFont != 'Impact' &&
                sFont != 'Lucida Console' && sFont != 'Lucida Sans Unicode' && sFont != 'MS Sans Serif' && sFont != 'MS Serif' && sFont != 'Palatino Linotype' && sFont != 'Tahoma' && sFont != 'Times New Roman' && sFont != 'Trebuchet MS' && sFont != 'Verdana') {
                LoadFont(sFont);
            }*/
        }
}