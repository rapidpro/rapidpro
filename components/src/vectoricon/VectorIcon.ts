import { customElement, property, LitElement, TemplateResult, html, css } from 'lit-element';
import { styleMap } from 'lit-html/directives/style-map.js';

@customElement("rp-icon")
export default class VectorIcon extends LitElement {

  static get styles() {
    return css`
      :host {
        display: inline-block;
        --icon-color: var(--color-text);
      }

      svg {
        transition: transform ease-in-out 150ms;
        fill: var(--icon-color);
      }
  `;
  }

  @property({type: String})
  prefix: string = '/sitestatic/icons/symbol-defs.svg#';

  @property({type: String})
  name: string;

  @property({type: Number})
  size: number = 16;

  @property({type: String})
  hoverColor: string = "#666";

  public render(): TemplateResult {

    const svgStyle = {
      width: `${this.size}px`,
      height:`${this.size}px`,
    }

    return html`
    <svg class="icon icon-${this.name}" style=${styleMap(svgStyle)}>
      <use href="${this.prefix}icon-${this.name}"></use>
    </svg>
    `;
  }
}