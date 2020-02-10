import { customElement, TemplateResult, html, css, property } from 'lit-element';
import FormElement from '../FormElement';

@customElement("rp-checkbox")
export default class Checkbox extends FormElement {
  
  static get styles() {
    return css`

      :host {
        font-family: var(--font-family);
        color: var(--color-text);
      }

      .checkbox-container {
        cursor: pointer;
        display: flex;
        user-select: none;
        -webkit-user-select: none;
      }

      .checkbox-label {
        padding: 0px;
        margin-left: 8px; 
        font-weight: 300;
        font-size: 14px;
        line-height: 19px;
      }
    `
  }

  @property({type: String})
  name: string;

  @property({type: Boolean})
  checked: boolean;

  public updated(changes: Map<string, any>) {
    super.updated(changes);
    if (changes.has("checked")) {
      if (this.checked) {
        this.setValue(1);
      } else {
        this.setValue(0);
      }
    } 
  }

  private handleClick(): void {
    this.checked = !this.checked;
  }

  public render(): TemplateResult {
    const iconName = this.checked ? "check-square" : "square";
    return html`
      <rp-field name=${this.name} .helpText=${this.helpText} .errors=${this.errors} .widgetOnly=${this.widgetOnly}>      
        <div class="checkbox-container" @click=${this.handleClick}>
            <rp-icon name=${iconName}></rp-icon><div class="checkbox-label">${this.label}</div>
        </div>
      </rp-field>
    `;
  }
}