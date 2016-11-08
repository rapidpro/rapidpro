#============================================================================
# Configure our app
#============================================================================
app = angular.module('app', ['ui.sortable', 'ui.bootstrap', 'ngAnimate', 'angularFileUpload', 'monospaced.elastic',
                             'temba.validation', 'temba.services', 'temba.controllers',
                             'temba.directives', 'temba.widgets'])

app.config [ '$httpProvider', '$sceDelegateProvider', ($httpProvider, $sceDelegateProvider) ->
  $httpProvider.defaults.xsrfCookieName = 'csrftoken'
  $httpProvider.defaults.xsrfHeaderName = 'X-CSRFToken'

  # we need to whitelist our urls to reference our recordings
  $sceDelegateProvider.resourceUrlWhitelist([
    'self',
    'http://*.s3.amazonaws.com/**',
    'https://*.s3.amazonaws.com/**',
    'http://textit.ngrok.com/**',
    'https://textit.ngrok.com/**',
    'http://textit.ngrok.io/**',
    'https://textit.ngrok.io/**',
  ])
]

#============================================================================
# Since Django uses {{ }}, we will have angular use [[ ]] instead.
#============================================================================
app.config [ '$compileProvider', '$interpolateProvider' , ($compileProvider, $interpolateProvider) ->
  $interpolateProvider.startSymbol "[["
  $interpolateProvider.endSymbol "]]"
  # $compileProvider.debugInfoEnabled false
]


angular.module('template/modal/backdrop.html', []).run [
  '$templateCache'
  ($templateCache) ->
    $templateCache.put 'template/modal/backdrop.html', '<div class="modal-backdrop"\n' + '     ng-style="{\'z-index\': 1040 + (index && 1 || 0) + index*10}"\n' + '></div>\n' + ''
    return
]
angular.module('template/modal/window.html', []).run [
  '$templateCache'
  ($templateCache) ->
    $templateCache.put 'template/modal/window.html', '<div tabindex="-1" role="dialog" class="modal" ng-style="{\'z-index\': 1050 + index*10, display: \'block\'}" ng-click="close($event)">\n' + '    <div class="modal-dialog" ng-class="{\'modal-sm\': size == \'sm\', \'modal-lg\': size == \'lg\'}"><div class="modal-content" ng-transclude></div></div>\n' + '</div>'
    return
]