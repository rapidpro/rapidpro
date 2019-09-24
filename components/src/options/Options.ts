import { customElement, TemplateResult, html, property, css } from 'lit-element';
import { CustomEventType } from '../interfaces';
import RapidElement, { EventHandler } from '../RapidElement';
import { styleMap } from 'lit-html/directives/style-map.js';

@customElement("rp-options")
export default class Options extends RapidElement {

  static get styles() {
    return css`
      .options-container {
        visibility: hidden;
        position: fixed;
        border-radius: var(--curvature);
        border: 0px solid var(--color-borders);
        box-shadow: 0px 0px 2px 0px rgb(204, 204, 204);
        background: #fff;
        z-index: 1;
      }

      .options {
        border-radius: var(--curvature);
        background: #fff;
        overflow-y: scroll;
        max-height: 225px;
        border: none;
      }

      .show {
        visibility: visible;
      }

      .option {
        font-size: 14px;
        padding: 7px 14px;
        border-radius: var(--curvature);
        margin: 3px;
        cursor: pointer;
        color: var(--color-text);
      }

      .option.focused {
        background: var(--color-selection);
        color: var(--color-text-light);
      }

      .option .detail {
        font-size: 85%;
        color: rgba(255,255,255,.9);
      }

      code {
        background: rgba(0,0,0,.15);
        padding: 1px 5px;
        border-radius: var(--curvature);
      }
    `
  }

  @property({type: Number})
  top: number;

  @property({type: Number})
  left: number;

  @property({type: Number})
  width: number;

  @property({type: Object})
  anchorTo: HTMLElement

  @property({type: Boolean})
  visible: boolean;

  @property({type: Number})
  cursorIndex: number = 0;

  @property({type: Array})
  options: any[]

  @property({attribute: false})
  renderOption: (option: any, selected: boolean) => void;

  @property({attribute: false})
  renderOptionName: (option: any, selected: boolean) => void;

  @property({attribute: false})
  renderOptionDetail: (option: any, selected: boolean) => void;

  public updated(changedProperties: Map<string, any>) {
    super.updated(changedProperties);

    // if our cursor changed, lets make sure our scrollbox is showing it
    if(changedProperties.has("cursorIndex")) {
      const focusedEle = this.shadowRoot.querySelector(".focused") as HTMLDivElement;
      if (focusedEle) {
        const scrollBox =  this.shadowRoot.querySelector(".options");
        const scrollBoxRect = scrollBox.getBoundingClientRect();
        const scrollBoxHeight = scrollBoxRect.height
        const focusedEleHeight = focusedEle.getBoundingClientRect().height;

        if (focusedEle.offsetTop + focusedEleHeight > scrollBox.scrollTop + scrollBoxHeight - 5) {
          const scrollTo = focusedEle.offsetTop - scrollBoxHeight + focusedEleHeight + 5;
          scrollBox.scrollTop = scrollTo;
        } else if (focusedEle.offsetTop < scrollBox.scrollTop) {
          const scrollTo = focusedEle.offsetTop - 5;
          scrollBox.scrollTop = scrollTo;
        }
      }
    }

    if(changedProperties.has("options")) {
      this.calculatePosition();
      this.cursorIndex = 0;
    }
  }

  private renderOptionDefault(option: any, selected: boolean): TemplateResult {
    const renderOptionName = (this.renderOptionName || this.renderOptionNameDefault);
    const renderOptionDetail = (this.renderOptionDetail || this.renderOptionDetailDefault);
    if (selected) {
      return html`<div class="name">${renderOptionName(option, selected)}</div><div class="detail">${renderOptionDetail(option, selected)}</div>`;
    } else {
      return html`<div class="name">${renderOptionName(option, selected)}</div>`;
    }
  }

  private renderOptionNameDefault(option: any, selected: boolean): TemplateResult {
    return html`${option.name}`
  }

  private renderOptionDetailDefault(option: any, selected: boolean): TemplateResult {
    return html`${option.detail}`
  }

  private handleSelection(tabbed: boolean = false) {
    const selected = this.options[this.cursorIndex];
    this.fireCustomEvent(CustomEventType.Selection, { selected, tabbed });
  }

  private moveCursor(direction: number): void {
    const newIndex = Math.max(Math.min(this.cursorIndex + direction, this.options.length - 1), 0);
    this.setCursor(newIndex);
  }

  private setCursor(newIndex: number): void {
    if (newIndex !== this.cursorIndex){
      this.cursorIndex = newIndex;
      this.fireCustomEvent(CustomEventType.CursorChanged, { index: newIndex });
    }
  }

  private handleKeyDown(evt: KeyboardEvent) {
    if (this.visible) {
      if ((evt.ctrlKey && evt.key === "n") || evt.key === "ArrowDown") {
        this.moveCursor(1);
        evt.preventDefault();
      } else if ((evt.ctrlKey && evt.key === "p") || evt.key === "ArrowUp") {
        this.moveCursor(-1);
        evt.preventDefault();
      } else if (evt.key === "Enter" || evt.key === "Tab") {
        this.handleSelection(evt.key === "Tab");
        evt.preventDefault();
      }

      if(evt.key === "Escape") {
        this.fireCustomEvent(CustomEventType.Canceled);
      }
    }
  }

  private calculatePosition() {
    const optionsBounds = this.shadowRoot.querySelector('.options-container').getBoundingClientRect();
    if (this.anchorTo) {
      const anchorBounds = this.anchorTo.getBoundingClientRect();   
      const topTop = anchorBounds.top - optionsBounds.height;

      if (topTop > 0 && anchorBounds.bottom + optionsBounds.height > window.innerHeight) {
        this.top = topTop; //  + window.pageYOffset;
      } else {
        this.top = anchorBounds.bottom; //  + window.pageYOffset;
      }

      this.left = anchorBounds.left;
      this.width = anchorBounds.width;
    }
  }

  public getEventHandlers(): EventHandler[] {
    return [
      { event: 'keydown', method: this.handleKeyDown },
      { event: 'scroll', method: this.calculatePosition }
    ]
  }

  public render(): TemplateResult {
    const renderOption = (this.renderOption || this.renderOptionDefault).bind(this);

    const containerStyle = {
      top: `${this.top}px`,
      left: `${this.left}px`,
      width: `${this.width}px`
    }

    const optionsStyle = {
      width: `${this.width}px`
    }

    return html`
      <div class="options-container ${this.visible ? "show": ""}" style=${styleMap(containerStyle)}>
        <div class="options" style=${styleMap(optionsStyle)}>
          ${this.options.map((option: any, index: number)=>html`
            <div 
              @mouseover=${(evt: MouseEvent)=>{
                  if (Math.abs(evt.movementX) + Math.abs(evt.movementY) > 0) {
                    this.setCursor(index);
                  }
              }}
              @click=${()=>{this.handleSelection();}}
              class="option ${index == this.cursorIndex ? 'focused' : ''}">
                ${renderOption(option, index == this.cursorIndex)}
            </div>
          `)}
        </div>
        <slot></slot>
      </div>
      `;
  }
}
