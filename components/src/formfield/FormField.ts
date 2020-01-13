import { customElement, TemplateResult, html, css, property, LitElement } from 'lit-element';

/**
 * A small wrapper to display labels and help text in a smartmin style.
 * This exists so we can display things consistently before restyling.
 */
@customElement("rp-field")
export default class FormField extends LitElement {
  static get styles() {
    return css`
      :host {
        font-family: var(--font-family);
      }

      label {
        margin-bottom: 4px;
        display: block;   
        font-weight: 300;
        font-size: 14px;
        line-height: inherit;

      }

      .help-text {
        font-size: 12px;
        line-height: inherit;
        color: var(--color-text-help);
        margin: 4px 0 14px;
      }

      rp-alert {
        margin-top: 10px;
      }
    }`
  }

  @property({type: Boolean, attribute: "widget_only"})
  widgetOnly: boolean;

  @property({type: Array, attribute: false})
  errors: string[] = [];

  @property({type: String, attribute: "help_text"})
  helpText: string;

  @property({type: String})
  label: string;

  @property({type: String})
  name: string;
  
  public render(): TemplateResult {

    const errors = (this.errors || []).map((error: string) => {
      return html`<rp-alert level="error">${error}</rp-alert>`;
    });


    if (this.widgetOnly) {
      return html`
        <slot></slot>
        ${errors}
      `
    }

    return html`
    ${this.name ? html`<label class="control-label" for="${this.name}">${this.label}</label>` : null}
    <slot></slot>
    ${this.helpText ? html`<div class="help-text">${this.helpText}</div>`: null}
    ${errors}

`;
  }

}