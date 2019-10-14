import { customElement, property, html, TemplateResult, css } from 'lit-element';
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
export default class Loading extends RapidElement {

  static get styles() {
    return css`
      @keyframes pulse {
        0% {
          transform: scale(0.2);
        }
        20% {
          transform: scale(1);
        }
        100% {
          transform: scale(0.2);
        }
      }

      .loading {
        padding: 5px;
      }

      .loading > div {
        border: 1px inset rgba(0, 0, 0, .05);
        display: inline-block;
        animation: pulse 0.9s cubic-bezier(0.3, 0, 0.7, 1) infinite;
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
    const loadingStyle = {
      width: (this.size * this.units * 2) + 10 + 'px',
      height: this.size + 'px'
    };

    if (!this.colorRGB) {
      return null;
    }

    return html`<div class="loading" style=${styleMap(loadingStyle)}>
        ${range(0, this.units).map((num: number) => {
          const ballStyle = {
            'border-radius': this.square ? '0' : '50%',
            width: this.size + 'px',
            height: this.size + 'px',
            margin: (this.size / 3) + 'px',
            animationDelay: `-${1 - num * (1 / this.units)}s`,
            background: `rgba(${this.colorRGB.r},${this.colorRGB.g},${
              this.colorRGB.b
            }, ${1 - num * (1 / this.units)})`
          }

          return html`<div
            style=${styleMap(ballStyle)}
          ></div>`
        })}
      </div>`
  }
}
