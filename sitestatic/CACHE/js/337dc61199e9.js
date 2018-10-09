!function($){"use strict"
var Limit=function(element,options){this.$element=$(element)
this.options=$.extend({},$.fn.limit.defaults,options)
this.maxChars=this.options.maxChars||this.maxChars
this.counter=$(this.options.counter)||this.counter
this.listen()
this.check()}
Limit.prototype={constructor:Limit,listen:function(){this.$element.on('keypress',$.proxy(this.keypress,this)).on('keyup',$.proxy(this.keyup,this))
if($.browser.webkit||$.browser.msie){this.$element.on('keydown',$.proxy(this.keypress,this))}},check:function(){this.query=this.$element.val()
if(!this.query){this.counter.text(this.maxChars)
this.counter.css('color','red')
this.$element.trigger('uncross')}
this.counter.text(this.maxChars-this.query.length)
if(this.query.length>this.maxChars){this.counter.css('color','red')
this.$element.trigger('cross')}else if(this.query.length>this.maxChars-10){this.counter.css('color','red')
this.$element.trigger('uncross')}else{this.counter.css('color','')
this.$element.trigger('uncross')}},keyup:function(e){this.check()
e.stopPropagation()
e.preventDefault()},keypress:function(e){this.check()
e.stopPropagation()}}
$.fn.limit=function(option){return this.each(function(){var $this=$(this),data=$this.data('limit'),options=typeof option=='object'&&option
if(!data)$this.data('limit',(data=new Limit(this,options)))
if(typeof option=='string')data[option]()})}
$.fn.limit.defaults={maxChars:140,counter:''}
$.fn.limit.Constructor=Limit
$(function(){$('body').on('focus.limit.data-api','[data-provide="limit"]',function(e){var $this=$(this)
if($this.data('limit'))return
e.preventDefault()
$this.limit($this.data())})})}(window.jQuery);