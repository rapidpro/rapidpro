const path = require('path');
const merge = require('webpack-merge');
const createDefaultConfig = require('@open-wc/building-webpack/modern-config');

const config = createDefaultConfig({
    input: path.resolve(__dirname, './public/index.html')
});

module.exports = merge(config, {
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
