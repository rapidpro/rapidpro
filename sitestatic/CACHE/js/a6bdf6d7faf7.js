if(typeof window.console!=='object'||typeof window.console.emulated==='undefined'){if(typeof window.console!=='object'||!(typeof window.console.log==='function'||typeof window.console.log==='object')){window.console={};window.console.log=window.console.debug=window.console.warn=window.console.trace=function(){};window.console.error=function(){var msg="An error has occured. More information will be available in the console log.";for(var i=0;i<arguments.length;++i){if(typeof arguments[i]!=='string'){break;}
msg+="\n"+arguments[i];}
if(typeof Error!=='undefined'){throw new Error(msg);}
else{throw(msg);}};}
else{if(typeof window.console.debug==='undefined'){window.console.debug=function(){var arr=['console.debug:'];for(var i=0;i<arguments.length;i++){arr.push(arguments[i]);};window.console.log.apply(window.console,arr);};}
if(typeof window.console.warn==='undefined'){window.console.warn=function(){var arr=['console.warn:'];for(var i=0;i<arguments.length;i++){arr.push(arguments[i]);};window.console.log.apply(window.console,arr);};}
if(typeof window.console.error==='undefined'){window.console.error=function(){var arr=['console.error'];for(var i=0;i<arguments.length;i++){arr.push(arguments[i]);};window.console.log.apply(window.console,arr);};}
if(typeof window.console.trace==='undefined'){window.console.trace=function(){window.console.error.apply(window.console,['console.trace does not exist']);};}}
window.console.emulated=true;}
(function($)
{if(!($.History||false)){$.History={options:{debug:false},state:'',$window:null,$iframe:null,handlers:{generic:[],specific:{}},extractHash:function(url){var hash=url.replace(/^[^#]*#/,'').replace(/^#+|#+$/,'');return hash;},getState:function(){var History=$.History;return History.state;},setState:function(state){var History=$.History;state=History.extractHash(state)
History.state=state;return History.state;},getHash:function(){var History=$.History;var hash=History.extractHash(window.location.hash||location.hash);return hash;},setHash:function(hash){var History=$.History;hash=History.extractHash(hash);if(typeof window.location.hash!=='undefined'){if(window.location.hash!==hash){window.location.hash=hash;}}else if(location.hash!==hash){location.hash=hash;}
return hash;},go:function(to){var History=$.History;to=History.extractHash(to);var hash=History.getHash(),state=History.getState();if(to!==hash){History.setHash(to);}else{if(to!==state){History.setState(to);}
History.trigger();}
return true;},hashchange:function(e){var History=$.History;var hash=History.getHash();History.go(hash);return true;},bind:function(state,handler){var History=$.History;if(handler){if(typeof History.handlers.specific[state]==='undefined'){History.handlers.specific[state]=[];}
History.handlers.specific[state].push(handler);}
else{handler=state;History.handlers.generic.push(handler);}
return true;},trigger:function(state){var History=$.History;if(typeof state==='undefined'){state=History.getState();}
var i,n,handler,list;if(typeof History.handlers.specific[state]!=='undefined'){list=History.handlers.specific[state];for(i=0,n=list.length;i<n;++i){handler=list[i];handler(state);}}
list=History.handlers.generic;for(i=0,n=list.length;i<n;++i){handler=list[i];handler(state);}
return true;},construct:function(){var History=$.History;$(document).ready(function(){History.domReady();});return true;},configure:function(options){var History=$.History;History.options=$.extend(History.options,options);return true;},domReadied:false,domReady:function(){var History=$.History;if(History.domRedied){return;}
History.domRedied=true;History.$window=$(window);History.$window.bind('hashchange',this.hashchange);setTimeout(History.hashchangeLoader,200);return true;},nativeSupport:function(browser){browser=browser||$.browser;var browserVersion=browser.version,browserVersionInt=parseInt(browserVersion,10),browserVersionParts=browserVersion.split(/[^0-9]/g),browserVersionPartsOne=parseInt(browserVersionParts[0],10),browserVersionPartsTwo=parseInt(browserVersionParts[1],10),browserVersionPartsThree=parseInt(browserVersionParts[2],10),nativeSupport=false;if((browser.msie||false)&&browserVersionInt>=8){nativeSupport=true;}
else if((browser.webkit||false)&&browserVersionInt>=528){nativeSupport=true;}
else if((browser.mozilla||false)){if(browserVersionPartsOne>1){nativeSupport=true;}
else if(browserVersionPartsOne===1){if(browserVersionPartsTwo>9){nativeSupport=true;}
else if(browserVersionPartsTwo===9){if(browserVersionPartsThree>=2){nativeSupport=true;}}}}
else if((browser.opera||false)){if(browserVersionPartsOne>10){nativeSupport=true;}
else if(browserVersionPartsOne===10){if(browserVersionPartsTwo>=60){nativeSupport=true;}}}
return nativeSupport;},hashchangeLoader:function(){var History=$.History;var nativeSupport=History.nativeSupport();if(!nativeSupport){var checker;if($.browser.msie){History.$iframe=$('<iframe id="jquery-history-iframe" style="display: none;"></$iframe>').prependTo(document.body)[0];History.$iframe.contentWindow.document.open();History.$iframe.contentWindow.document.close();var iframeHit=false;checker=function(){var hash=History.getHash();var state=History.getState();var iframeHash=History.extractHash(History.$iframe.contentWindow.document.location.hash);if(state!==hash){if(!iframeHit){History.$iframe.contentWindow.document.open();History.$iframe.contentWindow.document.close();History.$iframe.contentWindow.document.location.hash=hash;}
iframeHit=false;History.$window.trigger('hashchange');}
else{if(state!==iframeHash){iframeHit=true;History.setHash(iframeHash);}}};}
else{checker=function(){var hash=History.getHash();var state=History.getState();if(state!==hash){History.$window.trigger('hashchange');}};}
setInterval(checker,200);}
else{var hash=History.getHash();if(hash){History.$window.trigger('hashchange');}}
return true;}};$.History.construct();}
else{window.console.warn('$.History has already been defined...');}})(jQuery);