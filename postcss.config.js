const tailwindcss = require('tailwindcss');

module.exports = {
    syntax: 'postcss-scss',
    plugins: [
        require('tailwindcss'),
        require('postcss-simple-vars'),
        require('postcss-nested'),
        require('autoprefixer'),
        // require('cssnano')({
        // preset: 'default',
        //}),
    ],
};
