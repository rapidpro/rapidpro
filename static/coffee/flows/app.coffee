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

app.run ($rootScope, Flow, utils) ->

  $rootScope.oxford = (parts, quotes=false) ->
    result = ""
    if parts
      parts = utils.clone(parts)

      if quotes
        for part of parts
          parts[part] = '"' + parts[part] + '"'
      
      last = parts.pop()
      result = parts.join(', ') 
      
      if parts.length > 1
        result += ','

      if parts.length > 0
        result += ' and '
      
      result += last
    return result
  
  $rootScope.hasInvalidFields = (inputs) ->
    # find invalid field reference in any of our inputs
    completion = new AutoComplete(Flow.completions)
    for input in inputs
      completion.findInvalidFields(input)

    # set our invalid fields on our local scope
    this.invalidFields = completion.getInvalidFields()
    return this.invalidFields.length > 0

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