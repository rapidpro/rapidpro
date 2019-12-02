import { LitElement, TemplateResult, html, css, customElement, property } from 'lit-element';
import { getClasses } from '../utils';

@customElement("rp-button")
export default class Button extends LitElement {

  static get styles() {
    return css`

      :host {
        display: inline-block;
      }

      .button {
        background: blue;
        color: #fff;
        cursor: pointer;
        display: block;
        border-radius: var(--curvature);
        outline: none;
        transition: background ease-in 100ms;
        user-select: none;
        text-align: center;
      }

      .button:focus {
        outline: none;
        margin: 0;
      }

      .button:focus .mask{
        background: rgb(0,0,0,.1);
        box-shadow: 0 0 0px 1px var(--color-focus);
      }

      .button.secondary:focus .mask{
        background: transparent;
        box-shadow: 0 0 0px 1px var(--color-focus);
      }

      .mask {
        padding: 8px 14px;
        border-radius: var(--curvature);
        border: 1px solid transparent;
        transition: all ease-in 100ms;
      }

      .button.disabled {
        background: var(--color-button-disabled);
        color: rgba(255, 255, 255, .45);
      }

      .button.disabled .mask {
        box-shadow: 0 0 0px 1px var(--color-button-disabled);
      }

      .button.active .mask {
        box-shadow: inset 0 0 4px 2px rgb(0,0,0, .1);
      }

      .secondary.active {
        background: transparent;
        color: var(--color-text);
      }

      .secondary.active .mask{
        /* box-shadow: inset 0 0 4px 2px rgb(0,0,0, .1); */
        border: none;
      }

      .button.secondary.active:focus .mask {
        background: transparent;
        box-shadow: none;
      }


      .primary {
        background: var(--color-button-primary);
        color: var(--color-button-primary-text);
      }

      .secondary {
        background: transparent;
        color: var(--color-text);
      }

      .secondary:hover .mask{
        border: 1px solid var(--color-button-secondary);
      }

      .mask:hover {
        background: rgba(0,0,0,.1);
      }

      .secondary .mask:hover {
        background: transparent;
      }
      
      .name {
      }

      rp-loading {

      }
  `;
  }

  

  @property({type: Boolean})
  primary: boolean;

  @property({type: Boolean})
  secondary: boolean;

  @property()
  name: string;

  @property({type: Boolean})
  disabled: boolean;

  @property({type: Boolean})
  active: boolean;

  @property({type: String})
  href: string;

  private handleClick(evt: MouseEvent) {
    if (this.href) {
      this.ownerDocument.location.href = this.href;
      evt.preventDefault();
      evt.stopPropagation();
    }
  }

  private handleKeyUp(event: KeyboardEvent): void {
    this.active = false;
    if (event.key === "Enter") {
      this.click();
    }
  }

  private handleMouseDown(event: MouseEvent): void {
    if (!this.disabled) {
      this.active = true;
    }
  }

  private handleMouseUp(event: MouseEvent): void {
    this.active = false;
  }

  public render(): TemplateResult {
      return html`
        <div class="button 
          ${getClasses({ 
          "primary": this.primary,
          "secondary": this.secondary,
          "disabled": this.disabled,
          "active": this.active,
          })}"
          tabindex="0"
          @mousedown=${this.handleMouseDown}
          @mouseup=${this.handleMouseUp}
          @mouseleave=${this.handleMouseUp}
          @keyup=${this.handleKeyUp}
          @click=${this.handleClick}
        >
          <div class="mask">
            <div class="name">${this.name}</div>
          </div>
        </div>
      `;
  }
}