'use strict';
(function($) {
    angular
            .module('angularSortableDemo', [
                'sortable'
            ])
            .controller('SortableCtrl', ['$scope', function($scope) {
                    $scope.items = [
                        {
                            name: 'JB',
                            profession: 'King of rock',
                            color: '#ff0000',
                            subitems: ['a', 'b', 'c', 'd', 'e']
                        },
                        {
                            name: 'Fred Aster',
                            profession: 'Dancer',
                            color: '#f0f000',
                            subitems: ['a', 'b', 'c', 'd', 'e']
                        },
                        {
                            name: 'Albert Einstein',
                            profession: 'Physician',
                            color: '#f000f0',
                            subitems: ['a', 'b', 'c', 'd', 'e']
                        },
                        {
                            name: 'PK Subban',
                            profession: 'Hockey player',
                            color: '#0000ff',
                            subitems: ['a', 'b', 'c', 'd', 'e']
                        },
                        {
                            name: 'Mother Theresa',
                            profession: 'Saint',
                            color: '#00ff00',
                            subitems: ['a', 'b', 'c', 'd', 'e']
                        }
                    ];

                    $scope.onItemsDrag = function(event) {
                        // Do whatever you want here...
                        console.log('onItemsDrag');
                    };

                    $scope.onItemsDragstart = function(event) {
                        // Do whatever you want here...
                        console.log('onItemsDragstart');
                    };

                    $scope.onItemsDragend = function(event) {
                        // Do whatever you want here...
                        console.log('onItemsDragend');
                    };

                    $scope.onItemsChange = function(fromIdx, toIdx) {
                        // Do whatever you want here...
                        console.log('onItemsChange');
                    };
                }]);
}(jQuery));