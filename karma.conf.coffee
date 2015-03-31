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
      'static/js/jquery-2.1.0.min.js',
      'static/angular-1.3.0-beta.17/angular.js',
      'static/angular-1.3.0-beta.17/angular-animate.js'
      'static/angular-1.3.0-beta.17/angular-mocks.js',
      'static/angular/ui-bootstrap-tpls-0.11.0.js',
      'static/scripts/angular-file-upload-1.6.12/angular-file-upload.js',
      'static/scripts/angular-elastic-2.4.0/angular-elastic.js',
      'static/js/jquery.jsPlumb-1.6.3.js',
      'static/angular/sortable.js',
      'static/js/jasmine-jquery.js',
      'static/js/uuid.js',
      'static/coffee/flows/*.coffee',
  	  'karma/flows/services.coffee',
      { pattern: 'media/test_flows/*.json', watched: true, served: true, included: false }
    ]

    # list of files to exclude
    exclude: [
    ]


    # preprocess matching files before serving them to the browser
    # available preprocessors: https://npmjs.org/browse/keyword/karma-preprocessor
    preprocessors: {
      '**/*.coffee': ['coffee']
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
    reporters: ['progress']


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
    autoWatch: false


    # start these browsers
    # available browser launchers: https://npmjs.org/browse/keyword/karma-launcher
    browsers: ['PhantomJS']


    # Continuous Integration mode
    # if true, Karma captures browsers, runs the tests and exits
    singleRun: true
