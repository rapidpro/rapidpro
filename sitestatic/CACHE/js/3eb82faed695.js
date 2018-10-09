(function(window,angular,undefined){'use strict';angular.module('ui.sortable',[]).value('uiSortableConfig',{items:'> [ng-repeat],> [data-ng-repeat],> [x-ng-repeat]'}).directive('uiSortable',['uiSortableConfig','$timeout','$log',function(uiSortableConfig,$timeout,$log){return{require:'?ngModel',scope:{ngModel:'=',uiSortable:'='},link:function(scope,element,attrs,ngModel){var savedNodes;function combineCallbacks(first,second){var firstIsFunc=typeof first==='function';var secondIsFunc=typeof second==='function';if(firstIsFunc&&secondIsFunc){return function(){first.apply(this,arguments);second.apply(this,arguments);};}else if(secondIsFunc){return second;}
return first;}
function getSortableWidgetInstance(element){var data=element.data('ui-sortable');if(data&&typeof data==='object'&&data.widgetFullName==='ui-sortable'){return data;}
return null;}
function patchSortableOption(key,value){if(callbacks[key]){if(key==='stop'){value=combineCallbacks(value,function(){scope.$apply();});value=combineCallbacks(value,afterStop);}
value=combineCallbacks(callbacks[key],value);}else if(wrappers[key]){value=wrappers[key](value);}
if(!value&&(key==='items'||key==='ui-model-items')){value=uiSortableConfig.items;}
return value;}
function patchUISortableOptions(newVal,oldVal,sortableWidgetInstance){function addDummyOptionKey(value,key){if(!(key in opts)){opts[key]=null;}}
angular.forEach(callbacks,addDummyOptionKey);var optsDiff=null;if(oldVal){var defaultOptions;angular.forEach(oldVal,function(oldValue,key){if(!newVal||!(key in newVal)){if(key in directiveOpts){if(key==='ui-floating'){opts[key]='auto';}else{opts[key]=patchSortableOption(key,undefined);}
return;}
if(!defaultOptions){defaultOptions=angular.element.ui.sortable().options;}
var defaultValue=defaultOptions[key];defaultValue=patchSortableOption(key,defaultValue);if(!optsDiff){optsDiff={};}
optsDiff[key]=defaultValue;opts[key]=defaultValue;}});}
angular.forEach(newVal,function(value,key){if(key in directiveOpts){if(key==='ui-floating'&&(value===false||value===true)&&sortableWidgetInstance){sortableWidgetInstance.floating=value;}
opts[key]=patchSortableOption(key,value);return;}
value=patchSortableOption(key,value);if(!optsDiff){optsDiff={};}
optsDiff[key]=value;opts[key]=value;});return optsDiff;}
function getPlaceholderElement(element){var placeholder=element.sortable('option','placeholder');if(placeholder&&placeholder.element&&typeof placeholder.element==='function'){var result=placeholder.element();result=angular.element(result);return result;}
return null;}
function getPlaceholderExcludesludes(element,placeholder){var notCssSelector=opts['ui-model-items'].replace(/[^,]*>/g,'');var excludes=element.find('[class="'+placeholder.attr('class')+'"]:not('+notCssSelector+')');return excludes;}
function hasSortingHelper(element,ui){var helperOption=element.sortable('option','helper');return helperOption==='clone'||(typeof helperOption==='function'&&ui.item.sortable.isCustomHelperUsed());}
function getSortingHelper(element,ui,savedNodes){var result=null;if(hasSortingHelper(element,ui)&&element.sortable('option','appendTo')==='parent'){result=savedNodes.last();}
return result;}
function isFloating(item){return(/left|right/).test(item.css('float'))||(/inline|table-cell/).test(item.css('display'));}
function getElementScope(elementScopes,element){var result=null;for(var i=0;i<elementScopes.length;i++){var x=elementScopes[i];if(x.element[0]===element[0]){result=x.scope;break;}}
return result;}
function afterStop(e,ui){ui.item.sortable._destroy();}
function getItemIndex(item){return item.parent().find(opts['ui-model-items']).index(item);}
var opts={};var directiveOpts={'ui-floating':undefined,'ui-model-items':uiSortableConfig.items};var callbacks={receive:null,remove:null,start:null,stop:null,update:null};var wrappers={helper:null};angular.extend(opts,directiveOpts,uiSortableConfig,scope.uiSortable);if(!angular.element.fn||!angular.element.fn.jquery){$log.error('ui.sortable: jQuery should be included before AngularJS!');return;}
function wireUp(){scope.$watchCollection('ngModel',function(){$timeout(function(){if(!!getSortableWidgetInstance(element)){element.sortable('refresh');}},0,false);});callbacks.start=function(e,ui){if(opts['ui-floating']==='auto'){var siblings=ui.item.siblings();var sortableWidgetInstance=getSortableWidgetInstance(angular.element(e.target));sortableWidgetInstance.floating=isFloating(siblings);}
var index=getItemIndex(ui.item);ui.item.sortable={model:ngModel.$modelValue[index],index:index,source:ui.item.parent(),sourceModel:ngModel.$modelValue,cancel:function(){ui.item.sortable._isCanceled=true;},isCanceled:function(){return ui.item.sortable._isCanceled;},isCustomHelperUsed:function(){return!!ui.item.sortable._isCustomHelperUsed;},_isCanceled:false,_isCustomHelperUsed:ui.item.sortable._isCustomHelperUsed,_destroy:function(){angular.forEach(ui.item.sortable,function(value,key){ui.item.sortable[key]=undefined;});}};};callbacks.activate=function(e,ui){savedNodes=element.contents();var placeholder=getPlaceholderElement(element);if(placeholder&&placeholder.length){var excludes=getPlaceholderExcludesludes(element,placeholder);savedNodes=savedNodes.not(excludes);}
var connectedSortables=ui.item.sortable._connectedSortables||[];connectedSortables.push({element:element,scope:scope});ui.item.sortable._connectedSortables=connectedSortables;};callbacks.update=function(e,ui){if(!ui.item.sortable.received){ui.item.sortable.dropindex=getItemIndex(ui.item);var droptarget=ui.item.parent();ui.item.sortable.droptarget=droptarget;var droptargetScope=getElementScope(ui.item.sortable._connectedSortables,droptarget);ui.item.sortable.droptargetModel=droptargetScope.ngModel;element.sortable('cancel');}
var sortingHelper=!ui.item.sortable.received&&getSortingHelper(element,ui,savedNodes);if(sortingHelper&&sortingHelper.length){savedNodes=savedNodes.not(sortingHelper);}
savedNodes.appendTo(element);if(ui.item.sortable.received){savedNodes=null;}
if(ui.item.sortable.received&&!ui.item.sortable.isCanceled()){scope.$apply(function(){ngModel.$modelValue.splice(ui.item.sortable.dropindex,0,ui.item.sortable.moved);});}};callbacks.stop=function(e,ui){if(!ui.item.sortable.received&&('dropindex'in ui.item.sortable)&&!ui.item.sortable.isCanceled()){scope.$apply(function(){ngModel.$modelValue.splice(ui.item.sortable.dropindex,0,ngModel.$modelValue.splice(ui.item.sortable.index,1)[0]);});}else{if((!('dropindex'in ui.item.sortable)||ui.item.sortable.isCanceled())&&!angular.equals(element.contents(),savedNodes)){var sortingHelper=getSortingHelper(element,ui,savedNodes);if(sortingHelper&&sortingHelper.length){savedNodes=savedNodes.not(sortingHelper);}
savedNodes.appendTo(element);}}
savedNodes=null;};callbacks.receive=function(e,ui){ui.item.sortable.received=true;};callbacks.remove=function(e,ui){if(!('dropindex'in ui.item.sortable)){element.sortable('cancel');ui.item.sortable.cancel();}
if(!ui.item.sortable.isCanceled()){scope.$apply(function(){ui.item.sortable.moved=ngModel.$modelValue.splice(ui.item.sortable.index,1)[0];});}};wrappers.helper=function(inner){if(inner&&typeof inner==='function'){return function(e,item){var oldItemSortable=item.sortable;var index=getItemIndex(item);item.sortable={model:ngModel.$modelValue[index],index:index,source:item.parent(),sourceModel:ngModel.$modelValue,_restore:function(){angular.forEach(item.sortable,function(value,key){item.sortable[key]=undefined;});item.sortable=oldItemSortable;}};var innerResult=inner.apply(this,arguments);item.sortable._restore();item.sortable._isCustomHelperUsed=item!==innerResult;return innerResult;};}
return inner;};scope.$watchCollection('uiSortable',function(newVal,oldVal){var sortableWidgetInstance=getSortableWidgetInstance(element);if(!!sortableWidgetInstance){var optsDiff=patchUISortableOptions(newVal,oldVal,sortableWidgetInstance);if(optsDiff){element.sortable('option',optsDiff);}}},true);patchUISortableOptions(opts);}
function init(){if(ngModel){wireUp();}else{$log.info('ui.sortable: ngModel not provided!',element);}
element.sortable(opts);}
function initIfEnabled(){if(scope.uiSortable&&scope.uiSortable.disabled){return false;}
init();initIfEnabled.cancelWatcher();initIfEnabled.cancelWatcher=angular.noop;return true;}
initIfEnabled.cancelWatcher=angular.noop;if(!initIfEnabled()){initIfEnabled.cancelWatcher=scope.$watch('uiSortable.disabled',initIfEnabled);}}};}]);})(window,window.angular);