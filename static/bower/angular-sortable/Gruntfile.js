module.exports = function (grunt) {

    // Load grunt tasks automatically
    require('load-grunt-tasks')(grunt);

    // Time how long tasks take. Can help when optimizing build times
    require('time-grunt')(grunt);

    // Configurable paths for the application
    var appConfig = {
        dist: './dist',
        banner: '/*!\n<%= pkg.name %> - <%= pkg.version %>\n' +
                '<%= pkg.description %>\n' +
                'Build date: <%= grunt.template.today("yyyy-mm-dd") %> \n*/\n'
    };

    grunt.util.linefeed = '\n';

    grunt.initConfig({
        yeoman: appConfig,
        jshint: {
            options: {},
            all: [
                'Gruntfile.js',
                'angular-sortable.js'
            ]
        },
        karma: {
            unit: {configFile: 'test/karma.conf.js'},
            server: {configFile: 'test/karma.conf.js'},
            continuous: {configFile: 'test/karma.conf.js', background: true},
//            coverage: {
//                configFile: 'test/karma.conf.js',
//                reporters: ['progress', 'coverage'],
//                preprocessors: {'src/*.js': ['coverage']},
//                coverageReporter: {
//                    reporters: [{
//                            type: 'text'
//                        }, {
//                            type: 'lcov',
//                            dir: 'coverage/'
//                        }]
//                },
//                singleRun: true
//            },
//            junit: {
//                configFile: 'test/karma.conf.js',
//                reporters: ['progress', 'junit'],
//                junitReporter: {
//                    outputFile: 'junit/unit.xml',
//                    suite: 'unit'
//                },
//                singleRun: true
//            }
        },
        protractor: {
            e2e: {
                options: {
                    configFile: 'test/e2e/protractor.conf.js',
                    keepAlive: true
                }
            },
            debug: {
                options: {
                    configFile: 'test/e2e/protractor.conf.js',
                    keepAlive: true,
                    debug: true
                }
            }
        },
        clean: {
            dist: 'dist'
        },
        copy: {
            build: {
                src: ['angular-sortable.js'],
                dest: 'dist/js/'
            }
        },
        cssmin: {
            dist: {
//                options: {
//                    banner: '<%= yeoman.banner %>'
//                },
                files: [{
                        src: ['angular-sortable.css'],
                        dest: 'dist/css/angular-sortable.css'
                    }]
            }
        },
        uglify: {
//            options: {
//                banner: '<%= yeoman.banner %>'
//            },
            dist: {
                src: ['dist/js/angular-sortable.js'],
                dest: 'dist/js/angular-sortable.min.js'
            }
        },
        connect: {
            options: {
                port: 8000,
                base: '.'
            },
            server: {},
            build: {}
        }
    });

    grunt.registerTask('tests', [
        'connect:server',
        'karma:e2e',
        'karma:unit'
//        'protractor:e2e'
    ]);

    grunt.registerTask('build', [
        'jshint',
        'clean',
        'copy',
        'cssmin',
        'uglify'
    ]);

    grunt.registerTask('server', [
        'connect:server:keepalive'
    ]);
};
