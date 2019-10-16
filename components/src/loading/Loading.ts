import { customElement, property, html, TemplateResult, css, LitElement } from 'lit-element';
import RapidElement from '../RapidElement';
import { styleMap } from 'lit-html/directives/style-map';
import { range } from '../utils';

interface Color {
  r: number;
  g: number;
  b: number;
}

const hexToRgb = (hex: string): Color => {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return result
    ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16)
      }
    : null;
};


@customElement("rp-loading")
export default class Loading extends LitElement {

  static get styles() {
    return css`
      .unit {
        border: 1px inset rgba(0, 0, 0, .05);
        display: inline-block;
        animation: pulse 0.9s cubic-bezier(0.3, 0, 0.7, 1) infinite;
      }

      @keyframes pulse {
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
  color: string = "#2387ca";

  @property({type: Number})
  size: number = 5;

  @property({type: Number})
  units: number = 5;

  @property({type: Boolean})
  square?: boolean;

  private colorRGB: Color;

  public firstUpdated(changedProperties: Map<string, any>) {
    if (changedProperties.has("color")) {
      this.colorRGB = hexToRgb(this.color);
      this.requestUpdate();
    }
  }

  public render(): TemplateResult {

    const margin = this.size / 3;

    if (!this.colorRGB) {
      return html`<div></div>`;
    }

    return html`<div>
        ${range(0, this.units).map((num: number) => {
          const ballStyle = {
            'border-radius': this.square ? '0' : '50%',
            width: this.size + 'px',
            height: this.size + 'px',
            margin: margin + 'px',
            animationDelay: `-${1 - num * (1 / this.units)}s`,
            background: `rgba(${this.colorRGB.r},${this.colorRGB.g},${
              this.colorRGB.b
            }, 1)`
          }
          return html`<div class="unit" style=${styleMap(ballStyle)}></div>`
        })}
      </div>`
  }
}
