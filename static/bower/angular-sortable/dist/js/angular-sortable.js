(function ($) {
    'use strict';

    var events = {
        drag: 'mousemove touchmove',
        dragstart: 'mousedown touchstart',
        dragend: 'mouseup touchend',
        selectstart: 'selectstart'
    };
    
    var classes = {
        sorting: 'sortable-sorting',
        item: 'sortable-item',
        handle: 'sortable-handle',
        clone: 'sortable-clone',
        active: 'sortable-activeitem'
    };

    var $body = $(document.body);
    var debounceMs = 2;

    function debounce(fn, wait, immediate) {
        var timeout;
        return function () {
            var context = this, args = arguments;
            var later = function () {
                timeout = null;
                if (!immediate) {
                    fn.apply(context, args);
                }
            };
            var callNow = immediate && !timeout;
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
            if (callNow) {
                fn.apply(context, args);
            }
        };
    }

    function Sortable(element, options) {
        this.options = $.extend({
            items: '.sortable',
            handles: null,
            zindex: '9000',
            dragX: true,
            dragY: true,
            onChange: null,
            onDragstart: null,
            onDrag: null,
            onDragend: null
        }, options);

        var id = element.scope().$id;

        this.classes = {};
        for (var key in classes) {
            this.classes[key] = classes[key] + '-' + id;
        }

        this.deltaX = 0;
        this.deltaY = 0;

        this.enabled = null;
        this.state = null;
        this.$element = $(element);
        this.$activeItem = null;

        this.refresh();
    }

    Sortable.prototype.enable = function (enabled) {
        this.enabled = enabled;
    };
    
    Sortable.prototype.cleanup = function () {
        $body.attr('unselectable', self.bodyUnselectable)
                .off(events.dragend)
                .off(events.drag)
                .off(events.selectstart);
        
        this.getItems().off(events.dragstart);
    };
    
    Sortable.prototype.getItems = function() {
        return $(this.options.items, this.$element);
    };
    
    Sortable.prototype.refresh = function () {
        this.cleanup();
        
        var self = this;
        var $items = this.getItems();
        
        // fixed class used to mark the elements, makes sure the event target is not set to a child node
        // not using `this.options.items` because that one is a selector (can have any form) controlled by client code
        $items.addClass(self.classes.item);
        // adding unselectable to draggable items
        $items.attr('unselectable', 'on');

        if (this.enabled) {
            if (!this.$activeItem) {
                $items.bind(events.dragstart, function (e) {
                    $items.unbind(e);
                    self.dragstart(e);
                });
            }
        }
    };

    var detect = debounce(function (context, event) {
        var $items = context.getItems();

        // caching before loop
        var from = 0, to = $items.length;
        var item;

        var top = parseInt(context.$dragElement.css('top'), 10);
        var left = parseInt(context.$dragElement.css('left'), 10);
        var deltaX = left - context.left;
        var deltaY = top - context.top;
        
        if (!context.options.dragX) {
            if (deltaY > 0) {
                from = context.draggingIdx + 1;
            } else if(deltaY < 0) {
                to = context.draggingIdx;
            } else {
                return;
            }
        } else if (!context.options.dragY) {
            if (deltaX > 0) {
                from = context.draggingIdx + 1;
            } else if(deltaX < 0) {
                to = context.draggingIdx;
            } else {
                return;
            }
        }

        for (; from < to; from++) {
            item = $items[from];
            if (from === context.draggingIdx) {
                continue;
            }

            if ((context.options.dragY &&
                    top > item.offsetTop &&
                    top < item.offsetTop + item.offsetHeight) ||
                    (context.options.dragX &&
                    left > item.offsetLeft &&
                    left < item.offsetLeft + item.offsetWidth)) {
                context.options.onChange(context.draggingIdx, from);
                context.draggingIdx = from;
                context.dragged = true;
                context.left = left;
                context.top = top;
                break;
            }
        }

    }, debounceMs);

    Sortable.prototype.drag = function (event) {
        if (event.isPropagationStopped()) {
            return;
        }
        
        this.options.onDrag(event);
        
        if(this.options.dragY) {
            this.$dragElement.css('top', '+=' + (event.clientY - this.state.clientY));
        }
        if(this.options.dragX) {
            this.$dragElement.css('left', '+=' + (event.clientX - this.state.clientX));
        }

        detect(this, event);

        this.state = event;
    };

    Sortable.prototype.dragstart = function (event) {
        if (event.which !== 1 || event.isPropagationStopped()) {
            return;
        }
        
        var self = this;
        this.dragged = false;
        var $target = $(event.target);
        var $items = this.getItems();
        
        // make sure event.target is a handle
        if (this.options.handles) {
            // marking all handles with a css class in order to be able to detect them on drag start using `$.closest()`
            // regardless of what selector the client used
            // doing it here and not on refresh because when referesh runs, the child nodes of the ng-repeat are not fully rendered
            if (this.options.handles) {
                $items.find(this.options.handles).addClass(this.classes.handle);
            }
            if (!$target.closest('.' + this.classes.handle).length) {
                return;
            }
        }

        this.options.onDragstart(event);

        // makes sure event target is the sortable element, not some child
        event.target = (function () {
            if ($target.hasClass(self.classes.item)) {
                return event.target;
            }
            else {
                return $target.closest('.' + self.classes.item)[0];
            }
        })();

        self.bodyUnselectable = $body.attr('unselectable');
        $body.attr('unselectable', 'on');

        this.$activeItem = $(event.target).addClass(self.classes.active);
        var position = this.$activeItem.position();

        self.draggingIdx = Array.prototype.indexOf.call($items, self.$activeItem[0]);

        this.top = position.top;
        this.left = position.left;

        this.$dragElement = $(event.target).clone()
                .css({
                    'z-index': this.options.zindex,
                    width: this.$activeItem[0].offsetWidth,
                    height: this.$activeItem[0].offsetHeight,
                    top: position.top,
                    left: position.left
                })
                .removeClass(self.classes.item)
                .addClass(self.classes.clone)
                .appendTo(event.target.parentNode);

        this.$element.addClass(self.classes.sorting);

        this.getItems().off(events.dragstart);

        $body.bind(events.drag, function (e) {
            self.drag(e);
        })
                .bind(events.dragend, function (e) {
                    $body.unbind(e);
                    self.dragend(e);
                })
                .bind(events.selectstart, function (e) {
                    e.preventDefault();
                    return false;
                });

        this.state = event;
    };

    Sortable.prototype.dragend = function (event) {
        if (event.isPropagationStopped()) {
            return;
        }
        
        var self = this;
        self.draggingIdx = null;
        var $items = this.getItems();
        
        $body.attr('unselectable', self.bodyUnselectable)
                .off(events.drag)
                .off(events.dragend)
                .off(events.selectstart);
        
        
        $items.bind(events.dragstart, function (e) {
                    $items.unbind(e);
                    return self.dragstart(e);
                });

        if (!this.dragged) {
            this.state.originalEvent.target.click();
        } else {
//            event.stopPropagation();
            this.options.onDragend(event);
        }

        this.$activeItem.removeClass(this.classes.active);
        this.$activeItem = null;

        this.$element.removeClass(this.classes.sorting);

        this.$dragElement.remove();
    };

    var safeApply = function ($scope, fn) {
        var phase = $scope.$root.$$phase;
        if (phase === '$apply' || phase === '$digest') {
            if (fn && (typeof (fn) === 'function')) {
                fn();
            }
        } else {
            $scope.$root.$apply(fn);
        }
    };

    angular.module('sortable', [])
            .factory('ngSortableOptions', function () {
                return {
                };
            })
            .directive('ngSortable', ['ngSortableOptions', function (ngSortableOptions) {
                    return {
                        restrict: 'A',
                        scope: {
                            ngSortable: '=',
                            ngSortableDirection: '@',
                            ngSortableItems: '@',
                            ngSortableHandles: '@',
                            ngSortableZindex: '@',
                            ngSortableDisable: '=',
                            ngSortableOnChange: '=',
                            ngSortableOnDrag: '=',
                            ngSortableOnDragstart: '=',
                            ngSortableOnDragend: '='
                        },
                        link: function ($scope, $element, $attrs) {
                            var items = $scope.ngSortable;

                            if (!items) {
                                items = [];
                            }

                            function onChange(fromIdx, toIdx) {
                                safeApply($scope, function () {
                                    var temp = items.splice(fromIdx, 1);
                                    items.splice(toIdx, 0, temp[0]);
                                    if ($scope.ngSortableOnChange) {
                                        $scope.ngSortableOnChange(fromIdx, toIdx);
                                    } else if (ngSortableOptions.onChange) {
                                        ngSortableOptions.onChange(fromIdx, toIdx);
                                    }
                                });
                            }

                            var direction = $scope.ngSortableDirection || ngSortableOptions.direction;
                            var options = {
                                items: $scope.ngSortableItems || ngSortableOptions.items,
                                handles: $scope.ngSortableHandles || ngSortableOptions.handles,
                                zindex: $scope.ngSortableZindex || ngSortableOptions.zindex,
                                onChange: onChange,
                                onDrag: $scope.ngSortableOnDrag || ngSortableOptions.onDrag || $.noop,
                                onDragstart: $scope.ngSortableOnDragstart || ngSortableOptions.onDragstart || $.noop,
                                onDragend: $scope.ngSortableOnDragend || ngSortableOptions.onDragend || $.noop,
                                dragX: direction !== 'vertical',
                                dragY: direction !== 'horizontal'
                            };

                            var sortable = new Sortable($element, options);

                            $scope.$watch('ngSortableDisable', function () {
                                sortable.enable(!$scope.ngSortableDisable);
                            });

                            $scope.$watch(function(){ return $('.' + sortable.classes.item, $element).length; }, function () {
                                sortable.refresh();
                            });
                            
                            $element.on('$destroy', function(){
                                console.log('angular-sortable destroy');
                                sortable.cleanup();
                            });
                        }
                    };
                }]);

}(jQuery));