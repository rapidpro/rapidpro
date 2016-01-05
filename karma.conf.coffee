# Karma configuration

module.exports = (config) ->
  config.set

    # base path that will be used to resolve all patterns (eg. files, exclude)
    basePath: ''


    # frameworks to use
    # available frameworks: https://npmjs.org/browse/keyword/karma-adapter
    frameworks: ['jasmine']


    # list of files / patterns to load in the browser
    files: [

      # our javascript dependencies
      'static/js/jquery-2.1.0.min.js',
      'static/angular-1.3.15/angular.js',
      'static/angular-1.3.15/angular-animate.js'
      'static/angular-1.3.15/angular-mocks.js',
      'static/angular/ui-bootstrap-tpls-0.11.0.js',
      'static/scripts/angular-file-upload-1.6.12/angular-file-upload.js',
      'static/scripts/angular-elastic-2.4.0/angular-elastic.js',
      'static/js/dom.jsPlumb-1.7.5.js',
      'static/angular/sortable.js',
      'static/js/jasmine-jquery.js',
      'static/js/uuid.js',
      'static/js/excellent.js',
      'static/scripts/bootstrap/js/bootstrap.js',
      'static/js/select2.js',
      'karma/helpers.coffee',
      'karma/flows/helpers.coffee',

      # the code we are testing
      'static/coffee/flows/*.coffee',
      'static/coffee/completions.coffee',
      'static/coffee/temba.coffee',

      # our json fixtures
      { pattern: 'media/test_flows/*.json', watched: true, served: true, included: false },

      # our test files
      'karma/flows/test_services.coffee',
      'karma/flows/test_directives.coffee',
      'karma/flows/test_controllers.coffee',
      'karma/test_completions.coffee',
      'karma/test_temba.coffee',

      # paritals templates to be loaded by ng-html2js
      'templates/partials/*.haml'
    ]

    # list of files to exclude
    exclude: [
    ]


    # preprocess matching files before serving them to the browser
    # available preprocessors: https://npmjs.org/browse/keyword/karma-preprocessor
    preprocessors: {
      'templates/partials/*.haml': ["ng-html2js"],
      'karma/**/*.coffee': ['coffee'],
      'static/**/*.coffee': ['coverage']
    }

    ngHtml2JsPreprocessor: {
      # the name of the Angular module to create
      moduleName: "partials"
      cacheIdFromPath: (filepath) ->
        return filepath.replace('templates', '').replace('.haml', '')
    }

    # this makes sure that we get coffeescript line numbers instead
    # of the line number from the transpiled
    coffeePreprocessor:
      options:
        bare: true
        sourceMap: true
      transformPath: (path) ->
        path.replace /\.js$/, '.coffee'

    # test results reporter to use
    # possible values: 'dots', 'progress'
    # available reporters: https://npmjs.org/browse/keyword/karma-reporter
    reporters: ['progress', 'coverage']

    coverageReporter:
      type: 'html'
      dir: 'js-coverage/'

    # web server port
    port: 9876


    # enable / disable colors in the output (reporters and logs)
    colors: true


    # level of logging
    # possible values:
    # - config.LOG_DISABLE
    # - config.LOG_ERROR
    # - config.LOG_WARN
    # - config.LOG_INFO
    # - config.LOG_DEBUG
    logLevel: config.LOG_INFO


    # enable / disable watching file and executing tests whenever any file changes
    autoWatch: true


    # start these browsers
    # available browser launchers: https://npmjs.org/browse/keyword/karma-launcher
    browsers: ['PhantomJS']


    # Continuous Integration mode
    # if true, Karma captures browsers, runs the tests and exits
    singleRun: false
