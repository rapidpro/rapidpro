if(typeof console=="undefined"){this.console={log:function(msg){}};}
function getCookie(name){var cookieValue=null;if(document.cookie&&document.cookie!=''){var cookies=document.cookie.split(';');for(var i=0;i<cookies.length;i++){var cookie=jQuery.trim(cookies[i]);if(cookie.substring(0,name.length+1)==(name+'=')){cookieValue=decodeURIComponent(cookie.substring(name.length+1));break;}}}
return cookieValue;}
var csrftoken=getCookie('csrftoken');function csrfSafeMethod(method){return(/^(GET|HEAD|OPTIONS|TRACE)$/.test(method));}
$.ajaxSetup({crossDomain:false,beforeSend:function(xhr,settings){if(!csrfSafeMethod(settings.type)){xhr.setRequestHeader("X-CSRFToken",csrftoken);}}});$(document).ready(function(){$('iframe').each(function(){var url=$(this).attr("src");if(url.indexOf("youtube.com")>=0){if(url.indexOf("?")>=0){$(this).attr("src",url+"&wmode=transparent");}else{$(this).attr("src",url+"?wmode=transparent");}}});$('ul.nav li.dropdown').hover(function(){$(this).find('.dropdown-menu').stop(true,true).delay(200).fadeIn();},function(){$(this).find('.dropdown-menu').stop(true,true).delay(200).fadeOut();});$(".pollrun-select-btn").on('click',pollRunSelectHandle);});function pollRunSelect(pollRunId){$("input#pollrun").val(pollRunId);$("form[name=pollrun]").submit();}
function pollRunSelectHandle(){pollRunSelect($(this).data('id'));$("#pollrun-text > span.text").text($(this).text());}
function getCheckedIds(){return Array();}
bindRefreshBlock();function bindRefreshBlock(){$('[data-toggle=dropdown]').on('focus',function(){dropDownOpen=true;hideTooltip();checkBlockRefresh();});$('[data-toggle=dropdown]').on('blur',function(){dropDownOpen=false;hideTooltip();checkBlockRefresh();});}
var dropDownOpen=false;var rowsChecked=getCheckedIds().length>0;function checkBlockRefresh(){$("#pjax").data('no-pjax',(dropDownOpen||rowsChecked));}
function getStartTime(){if($("#later-option").attr("checked")){return moment(new Date($("#start-datetime-value").val()*1000));}else{return moment();}}
function getStartHour(){var time=getStartTime();var hour=time.getHours();if(hour>12){hour=hour-12+"pm";}else{hour+="am";}
return hour;}
function update_schedule(){}
function updateDailySelection(){var selected=0;$('.btn-group > .btn').each(function(){if($(this).hasClass('active')){selected+=parseInt($(this).attr("value"));}});$("#repeat-days-value").val(selected);}
function scheduleSelection(event){event.stopPropagation();if($(this).attr('data-toggle')!='button'){$(this).toggleClass('active');}
var selected=$('.btn-group > .btn.active').length;if(selected==0&&!$(this).hasClass('active')){$(this).toggleClass('active');}
updateDailySelection();}
function hideTooltip(){$(".tooltip").fadeOut();}
function updateFile(){var file=$("#csv_file").val();while(file.indexOf("\\")>=0){file=file.substring(file.indexOf("\\")+1,file.length);}
$("#file-field").val(file);}
function intersect(a,b){var ai=0,bi=0;var result=new Array();while(ai<a.length&&bi<b.length){if(a[ai]<b[bi]){ai++;}else if(a[ai]>b[bi]){bi++;}else{result.push(a[ai]);ai++;bi++;}}
return result;}
function numericComparator(a,b){return a-b;}
function messageTextareaLengthCheck(){var length=$(this).val().length;var messages=Math.ceil(length/160);var left=messages*160-length;if(messages<2){$("#counter").text(""+left);}else{$("#counter").text(""+left+" / "+messages);}}
function initMessageLengthCounter(textarea,counter){function onKeyUp(){var ta=$(textarea);if(ta){var val=ta.val()
var length=0;if(val){length=val.length;}
var messages=Math.ceil(length/160);var left=messages*160-length;if(length==0){$(counter).text(""+160);}else if(messages<2){$(counter).text(""+left);}else{$(counter).text(""+left+" / "+messages);}}}
$(textarea).live('keyup',onKeyUp);onKeyUp();}
function toggle_section(){var shrink;$(".form-section").each(function(){var visible=$(this);if(visible.find('.expanded').is(":visible")){hide_section(visible);shrink=visible;}});var row=$(this).parent('.form-section');if(!shrink||(shrink&&row.attr("id")!=shrink.attr("id"))){var expanded=row.find('.expanded');if(expanded.is(":visible")){hide_section(row);}else{expand_section(row);}}}
function hide_section(section){if(!section.hasClass("error")){section.addClass('expandable');}
try{eval("update_"+section.attr("id")+"()");}catch(e){}
section.find('.section-icon').animate({'font-size':'35px','width':'40px','height':'40px'},200,function(){});section.find('.expanded').hide();section.find('.summary').fadeIn('slow');}
function expand_section(section){section.removeClass('expandable');section.find('.section-icon').animate({'font-size':'80px','width':'100px','height':'100px'},200,function(){});section.find('.expanded').slideDown('fast');section.find('.summary').hide();}
function setDatetimeValue(datetimeText,datepickerInstance,nextStart){var datetime=null;if(nextStart){datetime=nextStart;}else{datetime=new Date(datetimeText.replace(" at",""));}
var seconds=parseInt(datetime.getTime()/1000);$('#start-datetime-value').val(seconds);update_schedule();}
function resetStartDatetime(){var datetime=$("#start-datetime");if(datetime.val()=="Later"){datetime.val("");}datetime.focus();}
function startDatetimeClick(){$("#later-option").click();}
function initializeVideoPlayer(element){videojs(element,{plugins:{vjsdownload:{beforeElement:'playbackRateMenuButton',textControl:'Download',name:'downloadButton'}}});}
function disposeVideoPlayer(element){var player=videojs.getPlayers()[element.playerId];if(player){player.dispose();}}