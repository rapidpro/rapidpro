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

      # our bower dependencies
      'static/bower/jquery/jquery.js',
      'static/bower/angular/angular.js',
      'static/bower/angular-animate/angular-animate.js',
      'static/bower/angular-mocks/angular-mocks.js',
      'static/js/temba-ui-bootstrap-tpls.js',
      'static/bower/angular-elastic/elastic.js',
      'static/bower/jsPlumb/dist/js/dom.jsPlumb-1.7.5.js'
      'static/bower/angular-ui-sortable/sortable.js',
      'static/bower/bootstrap/js/bootstrap-modal.js',
      'static/bower/select2/select2.js',

      # non-bower dependencies
      'static/lib/angular-file-upload-1.6.12/angular-file-upload.js',
      'static/lib/jasmine-jquery.js',
      'static/lib/uuid.js',

      # karma helpers
      'karma/helpers.coffee',
      'karma/flows/helpers.coffee',

      # our code
      'static/js/excellent.js',
      'static/js/omnibox.js',
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
