import { LitElement, TemplateResult, html, css, customElement, property } from 'lit-element';
import { getClasses } from '../utils';
import { styleMap } from 'lit-html/directives/style-map.js';

@customElement("rp-label")
export default class Label extends LitElement {

  static get styles() {
    return css`

      :host {
        display: inline-block;
      }

      .mask {
        padding: 3px 6px;
        border-radius: var(--curvature);
      }

      .label.clickable .mask:hover {
        background: rgb(0,0,0,.05);
      }

      .label {
        border-radius: 2px;
        font-size: 80%;
        font-weight: 400;
        border-radius: var(--curvature);
        background: tomato;
        color: #fff;
        text-shadow: 0 0.04em 0.04em rgba(0,0,0,0.35);
      }

      .primary {
        background: var(--color-label-primary);
        color: var(--color-label-primary-text);
      }

      .secondary {
        background: var(--color-label-secondary);
        color: var(--color-label-secondary-text);
        text-shadow: none;
      }

      .light {
        background: var(--color-overlay-light);
        color: var(--color-overlay-light-text);
        text-shadow: none;
      }

      .dark {
        background: var(--color-overlay-dark);
        color: var(--color-overlay-dark-text);
        text-shadow: none;
      }

      .clickable {
        cursor: pointer;
      }
  `;
  }

  @property({type: Boolean})
  clickable: boolean;
  
  @property({type: Boolean})
  primary: boolean;

  @property({type: Boolean})
  secondary: boolean;

  @property({type: Boolean})
  light: boolean;

  @property({type: Boolean})
  dark: boolean;

  @property()
  backgroundColor: string;

  @property()
  textColor: string;

  public render(): TemplateResult {

    const labelStyle = this.backgroundColor && this.textColor ? {
      background: `${this.backgroundColor}`,
      color: `${this.textColor}`
    } : {};

    return html`
      <div class="label ${getClasses({ 
        "clickable": this.clickable,
        "primary": this.primary,
        "secondary": this.secondary,
        "light": this.light,
        "dark": this.dark
        })}"
        style=${styleMap(labelStyle)}
       >
        <div class="mask">
          <slot></slot>
        </div>
      </div>
    `;
  }
}