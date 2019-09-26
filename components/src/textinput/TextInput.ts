import { customElement, TemplateResult, html, css, property } from 'lit-element';
import RapidElement from '../RapidElement';
import { styleMap } from 'lit-html/directives/style-map.js';

@customElement("rp-textinput")
export default class TextInput extends RapidElement {
  static get styles() {
    return css`
      
      .input-container {
        border-radius: var(--curvature-widget);
        /* overflow: hidden;*/
        cursor: text;
        background: var(--color-widget-bg);
        border: 1px solid var(--color-widget-border);
        box-shadow: none;
        transition: all ease-in-out 200ms;
        display: flex;
        flex-direction: row;
        flex-wrap: wrap;
        align-items: stretch;
      }

      .input-container:focus-within {
        border-color: var(--color-focus);
        background: var(--color-widget-bg-focused);
        
        /* box-shadow: var(--color-widget-shadow-focused) 1px 1px 3px 0px inset; */
        /* box-shadow: var(--color-widget-shadow-focused) 0px 0px 3px 0px; */
      }

      .input-container:hover {
        background: var(--color-widget-bg-focused);
      }

      textarea {
        height: 85%;
      }

      .textinput {
        padding: 8px;
        border: none;
        flex: 1;
        margin: 0;
        background: none;
        color: var(--color-text);
        font-size: 13px;
        cursor: text;
        resize: none;
        box-shadow: var(--color-widget-shadow-focused) 0 1px 1px 0px inset;
      }

      .textinput:focus {
        outline: none;
        box-shadow: none;
        cursor: text;
      }

      .textinput::placeholder {
        color: rgba(0,0,0,.15);
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

  public render(): TemplateResult {
    const containerStyle = {
      height: `${this.textarea ? '100%' : 'auto'}`
    }

    return html`
    <div class="input-container" style=${styleMap(containerStyle)} @click=${()=>{ (this.shadowRoot.querySelector(".textinput") as HTMLInputElement).focus()}}>
      <slot></slot>
      ${this.textarea ? html`
        <textarea class="textinput" 
          name=${this.name}
          placeholder=${this.placeholder}
          .value=${this.value}>
        </textarea>
      ` : html`
        <input class="textinput" 
          name=${this.name}
          type="text"
          placeholder=${this.placeholder}
          .value=${this.value}>
      `}
    </div>
    `;
  }
}
