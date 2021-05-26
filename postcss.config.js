module.exports = {
    // syntax: 'postcss-scss',
    plugins: [
        require('tailwindcss'),
        require('postcss-simple-vars'),
        require('postcss-nested'),
        require('autoprefixer'),
    ],
};
