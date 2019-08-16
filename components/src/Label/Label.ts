import { customElement, property } from 'lit-element/lib/decorators';
import { LitElement, TemplateResult, html, css } from 'lit-element';
import { getClasses } from '../utils';


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
        background: rgb(0,0,0,.1);
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

  @property()
  backgroundColor: string;

  @property()
  textColor: string;

  public render(): TemplateResult {
    return html`
      ${this.backgroundColor && this.textColor ? html`
        <style>
          .label {
            background: ${this.backgroundColor};
            color: ${this.textColor};
          }
        </style>
      `: null}

      <div class="label ${getClasses({ 
        "clickable": this.clickable,
        "primary": this.primary,
        "secondary": this.secondary
        })}"
       >
        <div class="mask">
          <slot></slot>
        </div>
      </div>
    `;
  }
}