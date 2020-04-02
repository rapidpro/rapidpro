import { customElement, property, html, TemplateResult, css, LitElement } from 'lit-element';
import RapidElement from '../RapidElement';
import { styleMap } from 'lit-html/directives/style-map';
import { range } from '../utils';

@customElement("rp-loading")
export default class Loading extends LitElement {

  static get styles() {
    return css`
      .loading-unit {
        border: 1px inset rgba(0, 0, 0, .05);
        display: inline-block;
        animation: loading-pulse 0.9s cubic-bezier(0.3, 0, 0.7, 1) infinite;
      }

      @keyframes loading-pulse {
        0% {
          transform: scale(0.2);
          opacity: .1;
        }
        20% {
          transform: scale(1);
          opacity: 1;
        }
        100% {
          transform: scale(0.2);
          opacity: .1;
        }
      }
    `;
  }

  @property({type: String})
  color: string = "var(--color-primary-dark)";

  @property({type: Number})
  size: number = 5;

  @property({type: Number})
  units: number = 5;

  @property({type: Boolean})
  square?: boolean;

  public render(): TemplateResult {

    const margin = this.size / 2;

    return html`<div>
        ${range(0, this.units).map((num: number) => {
          const ballStyle = {
            'border-radius': this.square ? '0' : '50%',
            width: this.size + 'px',
            height: this.size + 'px',
            margin: margin + 'px',
            animationDelay: `-${1 - num * (1 / this.units)}s`,
            background: this.color
          }
          return html`<div class="loading-unit" style=${styleMap(ballStyle)}></div>`
        })}
      </div>`
  }
}
