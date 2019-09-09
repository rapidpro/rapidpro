# Engage Branding

### Custom Theme
The custom theme should be modified within the /docker/static/brands/engage directory. There are two files: variables.less and styles.less. This is where the core Engage css is overwritten.

### Engage Logo
Engage relies heavily on fonts. The Engage logo is treated as a font and uses an SVG backup for browsers that don't support fonts. The process to generate the Engage logo is to first create as an SVG.  The SVG is imported into IcoMoon (https://icomoon.io) where ttf, eot, and woff fonts are generated. These files need to live in brands/font/fonts.  

### favicon
The favicon is located at brand/engage/engage.ico.
