app = angular.module('temba.validation', [])

#============================================================================
# Form validation
#============================================================================
REGEX_NUMBER = /^\-?\d+[\.\d+]*$/
REGEX_ALPHANUM = /^[a-z\d\-_\s]+$/i
REGEX_VARIABLE = /^[ ]*@.+$/i

# simple numeric test
app.directive "number", ->
  require: "ngModel"
  link: (scope, elm, attrs, ctrl) ->

    ctrl.$parsers.unshift (viewValue) ->

      if not viewValue or REGEX_NUMBER.test(viewValue)
        # it is valid
        ctrl.$setValidity "number", true
        return viewValue
      else
        # it is invalid, return undefined (no model update)
        ctrl.$setValidity "number", false
        return undefined

    return

# simple numeric test
app.directive "alphanum", ->
  require: "ngModel"
  link: (scope, elm, attrs, ctrl) ->

    ctrl.$parsers.unshift (viewValue) ->

      if not viewValue or REGEX_ALPHANUM.test(viewValue)
        # it is valid
        ctrl.$setValidity "alphanum", true
        return viewValue
      else
        # it is invalid, return undefined (no model update)
        ctrl.$setValidity "alphanum", false
        return undefined

    return

# evaluates that the current model type is lower than the provided model value
app.directive "lowerThan", ($log) ->
  link = ($scope, $element, $attrs, ctrl) ->
    validate = (viewValue) ->
      comparisonModel = $attrs.lowerThan

      left = parseFloat(viewValue)
      right = parseFloat(comparisonModel)

      if !isNaN(left) and !isNaN(right)
        # It's valid because we have nothing to compare against
        ctrl.$setValidity "lowerThan", true  if not viewValue or not comparisonModel

        # It's valid if model is lower than the model we're comparing against
        ctrl.$setValidity "lowerThan", left < right
      else
        ctrl.$setValidity "lowerThan", true

      return viewValue

    ctrl.$parsers.unshift validate
    ctrl.$formatters.push validate

    $attrs.$observe "lowerThan", (comparisonModel) ->
      # whenever the comparison model changes, we need to revalidate
      # angular render won't update if the value hasn't changed, so let's force it
      ctrl.$$lastCommittedViewValue = undefined
      ctrl.$$invalidModelValue = undefined

      # set our view value and validate accordingly
      ctrl.$setViewValue(ctrl.$viewValue, true, true)

    return

  return { require: "ngModel", link: link }

# Evaluates an operand against the currently selected rule config type
app.directive "validateType", ->
  link = ($scope, $element, $attrs, ctrl) ->

    validate = (viewValue) ->
      type = $attrs.validateType

      # evaluates numerics if the are the numeric types
      if type in ['eq', 'lt', 'gt']
        numeric = parseFloat(viewValue)
        ctrl.$setValidity("validateType", not isNaN(numeric) or REGEX_VARIABLE.test(viewValue))
      else
        ctrl.$setValidity("validateType", true)

      return viewValue

    # re-evaluate everything the config type changes (they change the select widget)
    ctrl.$parsers.unshift validate
    ctrl.$formatters.push validate
    $attrs.$observe "validateType", (comparisonModel) ->
      validate ctrl.$viewValue
    return

  require:"ngModel"
  link: link

