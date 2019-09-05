# \<alias-editor>

This webcomponent follows the [open-wc](https://github.com/open-wc/open-wc) recommendation.

## Installation
```bash
yarn install
```

## Usage
```html

  <script type="module">
    import '{{STATIC_URL}}components/rp-components.js';
  </script>

  <alias-editor endpoint="/adminboundary/" osmid="{{ object.osm_id }}"></alias-editor>

```
