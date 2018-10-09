/*!
 * jQuery UI Droppable 1.10.2
 * http://jqueryui.com
 *
 * Copyright 2013 jQuery Foundation and other contributors
 * Released under the MIT license.
 * http://jquery.org/license
 *
 * http://api.jqueryui.com/droppable/
 *
 * Depends:
 *	jquery.ui.core.js
 *	jquery.ui.widget.js
 *	jquery.ui.mouse.js
 *	jquery.ui.draggable.js
 */(function($,undefined){function isOverAxis(x,reference,size){return(x>reference)&&(x<(reference+size));}
$.widget("ui.droppable",{version:"1.10.2",widgetEventPrefix:"drop",options:{accept:"*",activeClass:false,addClasses:true,greedy:false,hoverClass:false,scope:"default",tolerance:"intersect",activate:null,deactivate:null,drop:null,out:null,over:null},_create:function(){var o=this.options,accept=o.accept;this.isover=false;this.isout=true;this.accept=$.isFunction(accept)?accept:function(d){return d.is(accept);};this.proportions={width:this.element[0].offsetWidth,height:this.element[0].offsetHeight};$.ui.ddmanager.droppables[o.scope]=$.ui.ddmanager.droppables[o.scope]||[];$.ui.ddmanager.droppables[o.scope].push(this);(o.addClasses&&this.element.addClass("ui-droppable"));},_destroy:function(){var i=0,drop=$.ui.ddmanager.droppables[this.options.scope];for(;i<drop.length;i++){if(drop[i]===this){drop.splice(i,1);}}
this.element.removeClass("ui-droppable ui-droppable-disabled");},_setOption:function(key,value){if(key==="accept"){this.accept=$.isFunction(value)?value:function(d){return d.is(value);};}
$.Widget.prototype._setOption.apply(this,arguments);},_activate:function(event){var draggable=$.ui.ddmanager.current;if(this.options.activeClass){this.element.addClass(this.options.activeClass);}
if(draggable){this._trigger("activate",event,this.ui(draggable));}},_deactivate:function(event){var draggable=$.ui.ddmanager.current;if(this.options.activeClass){this.element.removeClass(this.options.activeClass);}
if(draggable){this._trigger("deactivate",event,this.ui(draggable));}},_over:function(event){var draggable=$.ui.ddmanager.current;if(!draggable||(draggable.currentItem||draggable.element)[0]===this.element[0]){return;}
if(this.accept.call(this.element[0],(draggable.currentItem||draggable.element))){if(this.options.hoverClass){this.element.addClass(this.options.hoverClass);}
this._trigger("over",event,this.ui(draggable));}},_out:function(event){var draggable=$.ui.ddmanager.current;if(!draggable||(draggable.currentItem||draggable.element)[0]===this.element[0]){return;}
if(this.accept.call(this.element[0],(draggable.currentItem||draggable.element))){if(this.options.hoverClass){this.element.removeClass(this.options.hoverClass);}
this._trigger("out",event,this.ui(draggable));}},_drop:function(event,custom){var draggable=custom||$.ui.ddmanager.current,childrenIntersection=false;if(!draggable||(draggable.currentItem||draggable.element)[0]===this.element[0]){return false;}
this.element.find(":data(ui-droppable)").not(".ui-draggable-dragging").each(function(){var inst=$.data(this,"ui-droppable");if(inst.options.greedy&&!inst.options.disabled&&inst.options.scope===draggable.options.scope&&inst.accept.call(inst.element[0],(draggable.currentItem||draggable.element))&&$.ui.intersect(draggable,$.extend(inst,{offset:inst.element.offset()}),inst.options.tolerance)){childrenIntersection=true;return false;}});if(childrenIntersection){return false;}
if(this.accept.call(this.element[0],(draggable.currentItem||draggable.element))){if(this.options.activeClass){this.element.removeClass(this.options.activeClass);}
if(this.options.hoverClass){this.element.removeClass(this.options.hoverClass);}
this._trigger("drop",event,this.ui(draggable));return this.element;}
return false;},ui:function(c){return{draggable:(c.currentItem||c.element),helper:c.helper,position:c.position,offset:c.positionAbs};}});$.ui.intersect=function(draggable,droppable,toleranceMode){if(!droppable.offset){return false;}
var draggableLeft,draggableTop,x1=(draggable.positionAbs||draggable.position.absolute).left,x2=x1+draggable.helperProportions.width,y1=(draggable.positionAbs||draggable.position.absolute).top,y2=y1+draggable.helperProportions.height,l=droppable.offset.left,r=l+droppable.proportions.width,t=droppable.offset.top,b=t+droppable.proportions.height;switch(toleranceMode){case"fit":return(l<=x1&&x2<=r&&t<=y1&&y2<=b);case"intersect":return(l<x1+(draggable.helperProportions.width/2)&&x2-(draggable.helperProportions.width/2)<r&&t<y1+(draggable.helperProportions.height/2)&&y2-(draggable.helperProportions.height/2)<b);case"pointer":draggableLeft=((draggable.positionAbs||draggable.position.absolute).left+(draggable.clickOffset||draggable.offset.click).left);draggableTop=((draggable.positionAbs||draggable.position.absolute).top+(draggable.clickOffset||draggable.offset.click).top);return isOverAxis(draggableTop,t,droppable.proportions.height)&&isOverAxis(draggableLeft,l,droppable.proportions.width);case"touch":return((y1>=t&&y1<=b)||(y2>=t&&y2<=b)||(y1<t&&y2>b))&&((x1>=l&&x1<=r)||(x2>=l&&x2<=r)||(x1<l&&x2>r));default:return false;}};$.ui.ddmanager={current:null,droppables:{"default":[]},prepareOffsets:function(t,event){var i,j,m=$.ui.ddmanager.droppables[t.options.scope]||[],type=event?event.type:null,list=(t.currentItem||t.element).find(":data(ui-droppable)").addBack();droppablesLoop:for(i=0;i<m.length;i++){if(m[i].options.disabled||(t&&!m[i].accept.call(m[i].element[0],(t.currentItem||t.element)))){continue;}
for(j=0;j<list.length;j++){if(list[j]===m[i].element[0]){m[i].proportions.height=0;continue droppablesLoop;}}
m[i].visible=m[i].element.css("display")!=="none";if(!m[i].visible){continue;}
if(type==="mousedown"){m[i]._activate.call(m[i],event);}
m[i].offset=m[i].element.offset();m[i].proportions={width:m[i].element[0].offsetWidth,height:m[i].element[0].offsetHeight};}},drop:function(draggable,event){var dropped=false;$.each(($.ui.ddmanager.droppables[draggable.options.scope]||[]).slice(),function(){if(!this.options){return;}
if(!this.options.disabled&&this.visible&&$.ui.intersect(draggable,this,this.options.tolerance)){dropped=this._drop.call(this,event)||dropped;}
if(!this.options.disabled&&this.visible&&this.accept.call(this.element[0],(draggable.currentItem||draggable.element))){this.isout=true;this.isover=false;this._deactivate.call(this,event);}});return dropped;},dragStart:function(draggable,event){draggable.element.parentsUntil("body").bind("scroll.droppable",function(){if(!draggable.options.refreshPositions){$.ui.ddmanager.prepareOffsets(draggable,event);}});},drag:function(draggable,event){if(draggable.options.refreshPositions){$.ui.ddmanager.prepareOffsets(draggable,event);}
$.each($.ui.ddmanager.droppables[draggable.options.scope]||[],function(){if(this.options.disabled||this.greedyChild||!this.visible){return;}
var parentInstance,scope,parent,intersects=$.ui.intersect(draggable,this,this.options.tolerance),c=!intersects&&this.isover?"isout":(intersects&&!this.isover?"isover":null);if(!c){return;}
if(this.options.greedy){scope=this.options.scope;parent=this.element.parents(":data(ui-droppable)").filter(function(){return $.data(this,"ui-droppable").options.scope===scope;});if(parent.length){parentInstance=$.data(parent[0],"ui-droppable");parentInstance.greedyChild=(c==="isover");}}
if(parentInstance&&c==="isover"){parentInstance.isover=false;parentInstance.isout=true;parentInstance._out.call(parentInstance,event);}
this[c]=true;this[c==="isout"?"isover":"isout"]=false;this[c==="isover"?"_over":"_out"].call(this,event);if(parentInstance&&c==="isout"){parentInstance.isout=false;parentInstance.isover=true;parentInstance._over.call(parentInstance,event);}});},dragStop:function(draggable,event){draggable.element.parentsUntil("body").unbind("scroll.droppable");if(!draggable.options.refreshPositions){$.ui.ddmanager.prepareOffsets(draggable,event);}}};})(jQuery);