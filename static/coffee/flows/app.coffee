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
  ])
]

#============================================================================
# Since Django uses {{ }}, we will have angular use [[ ]] instead.
#============================================================================
app.config ($interpolateProvider) ->
  $interpolateProvider.startSymbol "[["
  $interpolateProvider.endSymbol "]]"

