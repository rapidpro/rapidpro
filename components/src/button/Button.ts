import { LitElement, TemplateResult, html, css, customElement, property } from 'lit-element';
import { getClasses } from '../utils';

@customElement("rp-button")
export default class Button extends LitElement {

  static get styles() {
    return css`
      .button {
        background: blue;
        color: #fff;
        cursor: pointer;
        display: inline-block;
        border-radius: var(--curvature);
        outline: none;
      }

      .button:focus {
        outline: none;
        margin: 0;
      }

      .button:focus .mask{
        background: rgb(0,0,0,.1);
        box-shadow: 0 0 0px 2px var(--color-button-primary);
      }

      .button.secondary:focus .mask{
        border: 1px solid var(--color-button-secondary);
        background: transparent;
        box-shadow: 0 0 0px 2px var(--color-button-secondary);
      }

      .mask {
        padding: 5px 10px;
        border-radius: var(--curvature);
        border: 1px solid transparent;
      }

      .primary {
        background: var(--color-button-primary);
        color: var(--color-button-primary-text);
      }

      .secondary {
        background: transparent;
        color: var(--color-button-secondary);
      }

      .secondary:hover .mask{
        border: 1px solid var(--color-button-secondary);
      }

      .button.progress{
        background: #ddd;
      }

      .button.progress:focus .mask {
        box-shadow: 0 0 0px 2px var(--color-button-secondary);
      }

      .mask:hover {
        background: rgb(0,0,0,.1);
      }

      .secondary .mask:hover {
        background: transparent;
      }

  `;
  }

  @property({type: Boolean})
  primary: boolean;

  @property({type: Boolean})
  secondary: boolean;

  @property()
  name: string;

  @property()
  inProgessName: string;

  @property({type: Boolean})
  isProgress: boolean;

  public setProgress(progress: boolean): void {
    this.isProgress = progress;
  }

  private handleKeyUp(event: KeyboardEvent): void {
    if (event.key === "Enter") {
      this.click();
    }
  }

  public render(): TemplateResult {
      return html`
        <div class="button 
          ${getClasses({ 
          "progress": this.isProgress,
          "primary": this.primary,
          "secondary": this.secondary
          })}"
          tabindex="0"
          @keyup=${this.handleKeyUp}
        >
          <div class="mask">
            ${this.isProgress ? this.inProgessName || this.name : this.name}
          </div>
        </div>
      `;
  }
}