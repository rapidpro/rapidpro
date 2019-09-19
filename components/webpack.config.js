const path = require('path');
const merge = require('webpack-merge');
const { createCompatibilityConfig } = require('@open-wc/building-webpack');

const BundleTracker = require('webpack-bundle-tracker');

/* const config = createDefaultConfig({
    input: path.resolve(__dirname, './public/index.html')
});*/

const configs = createCompatibilityConfig({
    input: path.resolve(__dirname, './public/index.html')
});

module.exports = configs.map(config => {
    const legacy = config.output.filename.indexOf('legacy') === 0;
    const prefix = legacy ? 'legacy/' : '';
    const outputChunkFilename = `${prefix}${'chunk-[id].js'}`;

    const conf = merge(config, {
        resolve: {
            extensions: ['.ts', '.js', '.json', '.css']
        },
        output: {
            path: path.resolve(process.cwd(), '..', 'static', 'components'),
            library: 'rp-components',
            libraryTarget: 'umd',
            filename: prefix + 'rp-components.js',
            chunkFilename: outputChunkFilename
        },
        devtool: 'inline-source-map',
        module: {
            rules: [
                { test: /\.ts$/, loader: 'ts-loader' },
                {
                    test: /\.scss$/,
                    use: [
                        'style-loader',
                        // MiniCssExtractPlugin.loader,
                        'css-loader',
                        'sass-loader'
                    ]
                },
                { test: /\.css$/, use: ['style-loader', 'css-loader'] },
                { test: /\.(png|svg|jpg|gif)$/, loader: 'file-loader' }
            ]
        }
    });

    return conf;
});

/* module.exports = merge(config, {
    resolve: {
        extensions: ['.ts', '.js', '.json', '.css']
    },
    output: {
        path: path.resolve(process.cwd(), '..', 'static', 'components'),
        library: 'rp-components',
        libraryTarget: 'umd',
        filename: 'rp-components.js'
    },
    devtool: 'inline-source-map',
    module: {
        rules: [
            { test: /\.ts$/, loader: 'ts-loader' },
            {
                test: /\.scss$/,
                use: [
                    'style-loader',
                    // MiniCssExtractPlugin.loader,
                    'css-loader',
                    'sass-loader'
                ]
            },
            { test: /\.css$/, use: ['style-loader', 'css-loader'] },
            { test: /\.(png|svg|jpg|gif)$/, loader: 'file-loader' }
        ]
    }
});
*/
