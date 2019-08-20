import { customElement, property } from 'lit-element/lib/decorators';
import { LitElement, TemplateResult, html, css } from 'lit-element';


@customElement("rp-icon")
export default class FontIcon extends LitElement {

  static get styles() {
    return css`
      :host {
        display: inline-block;
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
  color: string = "#999";

  @property({type: String})
  hoverColor: string = "#666";

  public render(): TemplateResult {
    return html`
    <style>
      svg:hover {
        fill:${this.hoverColor};
      }

      svg {
        fill:${this.color};
        width:${this.size}px;
        height:${this.size}px;
      }
    </style>
    <svg class="icon icon-${this.name}">
      <use href="${this.prefix}icon-${this.name}"></use>
    </svg>
    `;
  }
}