import { css, customElement, html, LitElement, property, TemplateResult } from 'lit-element';

@customElement("rp-alert")
export default class Alert extends LitElement {

  static get styles() {
    return css`

      :host {
        display: block;
      }

      .rp-alert {
        color: var(--color-text-dark);
        padding: 8px;
        border-left: 6px inset rgba(0,0,0,.2);
        border-radius: var(--curvature-widget);
        font-size: 12px;
      }

      .rp-info {
        background: var(--color-info);
      }

      .rp-warning {
        background: var(--color-warning);
      }

      .rp-error {
        border-left: 6px solid var(--color-error);
        color: var(--color-error);
      }
    `;
  }

  @property({type: String})
  level: string = 'info';
  
  public render(): TemplateResult {
    return html`<div class="rp-alert rp-${this.level}"><slot></slot></div>`;
  }
}