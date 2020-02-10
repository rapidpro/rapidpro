import { customElement, TemplateResult, html, css, property } from 'lit-element';
import { styleMap } from 'lit-html/directives/style-map.js';
import FormElement from '../FormElement';

@customElement("rp-textinput")
export default class TextInput extends FormElement {
  static get styles() {
    return css`
      
      :host {
        font-family: var(--font-family);
      }

      .input-container {
        border-radius: var(--curvature-widget);
        cursor: text;
        background: var(--color-widget-bg);
        border: 1px solid var(--color-widget-border);
        box-shadow: none;
        transition: all ease-in-out 200ms;
        display: flex;
        flex-direction: row;
        align-items: stretch;
      }

      .input-container:focus-within {
        border-color: var(--color-focus);
        background: var(--color-widget-bg-focused);
        box-shadow: var(--widget-box-shadow-focused);
      }

      .input-container:hover {
        background: var(--color-widget-bg-focused);
      }

      textarea {
        height: var(--textarea-height);
      }

      .textinput {
        padding: 9px;
        border: none;
        flex: 1;
        margin: 0;
        background: none;
        color: var(--color-widget-text);
        font-size: 13px;
        cursor: text;
        resize: none;
        font-weight: 300;
        width: 100%;
      }

      .textinput:focus {
        outline: none;
        box-shadow: none;
        cursor: text;
      }

      .textinput::placeholder {
        color: var(--color-placeholder);
        
        font-weight: 200;
      }
    `
  }

  @property({type: Boolean})
  textarea: boolean;

  @property({type: String})
  placeholder: string = "";

  @property({type: String})
  value: string = "";

  @property({type: String})
  name: string = "";

  @property({type: Object})
  inputElement: HTMLInputElement;

  public firstUpdated(changes: Map<string, any>) {
    super.firstUpdated(changes);
    this.inputElement = this.shadowRoot.querySelector(".textinput");
  }

  public updated(changes: Map<string, any>) {
    super.updated(changes);
    if (changes.has("value")) {
      this.setValues([this.value]);
    }
  }

  private handleChange(update: any): void { 
    this.value = update.target.value;
  }

  /** we just return the value since it should be a string */
  public serializeValue(value: any): string {
    return value;
  }

  // TODO make this a formelement and have contactsearch set the root
  public render(): TemplateResult {
    const containerStyle = {
      height: `${this.textarea ? '100%' : 'auto'}`
    }

    return html`
    <rp-field name=${this.name} .label=${this.label} .helpText=${this.helpText} .errors=${this.errors} .widgetOnly=${this.widgetOnly}>
      <div class="input-container" style=${styleMap(containerStyle)} @click=${()=>{ (this.shadowRoot.querySelector(".textinput") as HTMLInputElement).focus()}}>
        ${this.textarea ? html`
          <textarea class="textinput" 
            name=${this.name}
            placeholder=${this.placeholder}
            @input=${this.handleChange}
            .value=${this.value}>
          </textarea>
        ` : html`
          <input class="textinput" 
            name=${this.name}
            type="text"
            @input=${this.handleChange}
            placeholder=${this.placeholder}
            .value=${this.value}>
        `}
        <slot></slot>
      </div>
    </rp-field>
    `;
  }
}
