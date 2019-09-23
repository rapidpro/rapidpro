const path = require('path');
const merge = require('webpack-merge');
const { createDefaultConfig } = require('@open-wc/building-webpack');
const WebpackIndexHTMLPlugin = require('@open-wc/webpack-index-html-plugin');
const getDefaultMode = require('@open-wc/building-webpack/src/get-default-mode');
const CreateIncludesPlugin = require('./CreateIncludesPlugin');
const mode = getDefaultMode();
const configs = [
    createDefaultConfig({
        input: path.resolve(__dirname, './public/index.html')
    })
];

module.exports = configs.map(config => {
    const legacy = config.output.filename.indexOf('legacy') === 0;
    const prefix = legacy ? 'legacy/' : '';
    // const outputChunkFilename = `${prefix}${'chunk-[id].js'}`;

    const conf = merge(config, {
        resolve: {
            extensions: ['.ts', '.js', '.json', '.css']
        },
        output: {
            path: path.resolve(process.cwd(), '..', 'static', 'components'),
            library: 'rp-components',
            libraryTarget: 'umd',
            filename: `${prefix}rp-components.[hash].js`
        },
        module: {
            rules: [
                { test: /\.ts$/, loader: 'ts-loader' },
                {
                    test: /\.scss$/,
                    use: ['style-loader', 'css-loader', 'sass-loader']
                },
                { test: /\.css$/, use: ['style-loader', 'css-loader'] },
                { test: /\.(png|svg|jpg|gif)$/, loader: 'file-loader' }
            ]
        },
        devtool: mode !== 'production' ? 'inline-source-map' : false
    });

    if (mode === 'production') {
        conf['entry'] = path.resolve(__dirname, './src/rp-components.js');
        conf['plugins'][1] = new WebpackIndexHTMLPlugin({
            template: ({ assets, entries, legacyEntries, variation }) => ``,
            polyfills: {
                coreJs: true,
                regeneratorRuntime: true,
                webcomponents: true,
                fetch: true
            },
            loader: 'external'
        });

        // create our django template includes
        conf['plugins'].push(
            new CreateIncludesPlugin({
                templates: path.resolve(
                    process.cwd(),
                    '..',
                    'templates',
                    'includes'
                )
            })
        );
    }
    return conf;
});
