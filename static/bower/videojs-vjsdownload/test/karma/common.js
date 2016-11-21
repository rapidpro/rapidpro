var merge = require('lodash-compat/object/merge');

var DEFAULTS = {
  basePath: '../..',
  frameworks: ['browserify', 'qunit'],

  files: [
      'dist/videojs-vjsdownload.css',
    'node_modules/sinon/pkg/sinon.js',
    'node_modules/sinon/pkg/sinon-ie.js',
    'node_modules/video.js/dist/video.js',
    'test/**/*.js'
  ],

  exclude: [
    'test/bundle.js'
  ],

  plugins: [
    'karma-browserify',
    'karma-qunit'
  ],

  preprocessors: {
    'test/**/*.js': ['browserify']
  },

  reporters: ['dots'],
  port: 9876,
  colors: true,
  autoWatch: false,
  singleRun: true,
  concurrency: Infinity,

  browserify: {
    transform: [
      'babelify',
      'browserify-shim'
    ]
  }
};

/**
 * Customizes target/source merging with lodash merge.
 *
 * @param  {Mixed} target
 * @param  {Mixed} source
 * @return {Mixed}
 */
var customizer = function(target, source) {
  if (Array.isArray(target)) {
    return target.concat(source);
  }
};

/**
 * Generates a new Karma config with a common set of base configuration.
 *
 * @param  {Object} custom
 *         Configuration that will be deep-merged. Arrays will be
 *         concatenated.
 * @return {Object}
 */
module.exports = function(custom) {
  return merge({}, custom, DEFAULTS, customizer);
};
