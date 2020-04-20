Highcharts is a JavaScript charting library based on SVG, with fallbacks to VML and canvas for old browsers. This package also contains Highstock, the financial charting package, and Highmaps for geo maps.

_For NPM users, please note that this module replaces the previous [Highcharts Server](https://www.npmjs.com/package/highcharts-server) module._

* Official website:  [www.highcharts.com](http://www.highcharts.com)
* Download page: [www.highcharts.com/download](http://www.highcharts.com/download)
* Licensing: [www.highcharts.com/license](http://www.highcharts.com/license)
* Support: [www.highcharts.com/support](http://www.highcharts.com/support)
* Issues: [Working repo](https://github.com/highcharts/highcharts/issues)

## Example Usage in Node/Browserify/Webpack
Please note that there are several ways to use Highcharts. For general installation instructions, see [the docs](http://www.highcharts.com/docs/getting-started/installation).

First, install the highcharts package.
```
npm install highcharts
```

Now load Highcharts in your project.
```js
// Load Highcharts
var Highcharts = require('highcharts');

// Alternatively, this is how to load Highstock or Highmaps
// var Highcharts = require('highcharts/highstock');
// var Highcharts = require('highcharts/highmaps');

// This is how a module is loaded. Pass in Highcharts as a parameter.
require('highcharts/modules/exporting')(Highcharts);

// Generate the chart
var chart = Highcharts.chart('container', {
	series: [{
		data: [1, 3, 2, 4]
	}],
  	// ... more options - see http://api.highcharts.com/highcharts
});
```
