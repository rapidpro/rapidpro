const fs = require('fs');
const path = require('path');
let templates = '';

function CreateIncludesPlugin(options) {
    // where we will put our django templates
    templates = options.templates;
}

function getScript(filename, nomodule) {
    return (
        '<script src="{{STATIC_URL}}components/' +
        filename +
        '"' +
        (nomodule ? ' nomodule="">' : '>') +
        '</script>\n'
    );
}

CreateIncludesPlugin.prototype.apply = function(compiler) {
    let loaderFile = '';
    const bodyScripts = [];

    compiler.plugin(
        'emit',
        function(compilation, callback) {
            Object.keys(compilation.assets).forEach(function(filename) {
                // The loader file is responsible for bringing in polyfills as
                // necessary. It references files that need to be located
                // inside our static directory
                if (filename.startsWith('loader.')) {
                    loaderFile = 'prefixed.' + filename;
                    let loaderSource = compilation.assets[filename].source();
                    loaderSource = loaderSource.replace(
                        /\"\.\/rp-components/g,
                        'static_url + "components/rp-components'
                    );
                    loaderSource = loaderSource.replace(
                        /\"polyfills\//g,
                        'static_url + "components/polyfills/'
                    );

                    fs.writeFileSync(
                        path.resolve(compiler.options.output.path, loaderFile),
                        loaderSource
                    );
                }

                // our main components file, it'll be included in the head of our template
                if (filename.startsWith('rp-components')) {
                    fs.writeFileSync(
                        path.resolve(templates, 'components-head.html'),
                        '<link rel="preload" href="{{STATIC_URL}}components/' +
                            filename +
                            '" as="script"></link>'
                    );
                }

                // we have some polyfills that are always present in our body, keep track of those here
                if (
                    filename.indexOf('.js.map') === -1 &&
                    (filename.startsWith('polyfills/core-js') ||
                        filename.startsWith('polyfills/regenerator'))
                ) {
                    bodyScripts.push(filename);
                }
            });

            // our main body template has a couple universal polyfills and our dynamic loader for any remaining
            // ones the current browser might need
            fs.writeFileSync(
                path.resolve(templates, 'components-body.html'),
                getScript(bodyScripts[0], true) +
                    getScript(bodyScripts[1], true) +
                    getScript(loaderFile, false)
            );
            callback();
        }.bind(this)
    );

    compiler.plugin('done', function() {
        console.log('\x1b[36m%s\x1b[0m', 'Generated templates in ' + templates);
        console.log(
            '\x1b[36m%s\x1b[0m',
            'Generated static includes in ' +
                compiler.options.output.path +
                '/components'
        );
    });
};

module.exports = CreateIncludesPlugin;
