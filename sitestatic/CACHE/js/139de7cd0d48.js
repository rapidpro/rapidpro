var app=angular.module("analytics",['ui.sortable']);app.config(function($interpolateProvider){$interpolateProvider.startSymbol("[[")
$interpolateProvider.endSymbol("]]")});