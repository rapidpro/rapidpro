import { customElement, TemplateResult, html, property, css } from 'lit-element';
import { CustomEventType } from '../interfaces';
import RapidElement, { EventHandler } from '../RapidElement';

@customElement("rp-options")
export default class Options extends RapidElement {

  static get styles() {
    return css`
      .options {
        overflow-y: scroll;
        background: #fff;
        border-radius: 5px;
        position: absolute;
        border: 1px solid var(--color-borders);
        box-shadow: 0px 0px 3px 1px rgba(0,0,0,.06);
        transition: opacity ease-in-out 200ms, top ease-in-out 100ms; 
        max-height: 300px;
        opacity: 0;
        visibility: hidden;
      }

      .show {
        opacity: 1;
        visibility: visible;
      }

      .option {
        font-size: 14px;
        padding: 10px 20px;
        border-radius: 5px;
        margin: 5px;
        cursor: pointer;
        color: var(--color-text);
      }

      .option.focused {
        background: rgba(var(--primary-rgb), .8);
        color: var(--color-text-light);
      }

      .option .detail {
        font-size: 80%;
        color: rgba(255,255,255,.6);
      }
    `
  }

  @property({type: Number})
  top: number;

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
        const scrollBoxHeight = scrollBox.getBoundingClientRect().height
        const focusedEleHeight = focusedEle.getBoundingClientRect().height;              
        if (focusedEle.offsetTop + focusedEleHeight > scrollBox.scrollTop + scrollBoxHeight - 5) {
          const scrollTo = focusedEle.offsetTop - scrollBoxHeight + focusedEleHeight + 5;
          scrollBox.scrollTo({ top: scrollTo });
        } else if (focusedEle.offsetTop < scrollBox.scrollTop) {
          const scrollTo = focusedEle.offsetTop - 5;
          scrollBox.scrollTo({ top: scrollTo });
        }
      }
    }

    if(changedProperties.has("options")) {
      this.calculatePosition();
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

  private handleSelection() {
    const selected = this.options[this.cursorIndex];
    this.fireEvent(CustomEventType.Selection, { selected })
  }

  private moveCursor(direction: number): void {
    const newIndex = Math.max(Math.min(this.cursorIndex + direction, this.options.length - 1), 0);
    this.setCursor(newIndex);
  }

  private setCursor(newIndex: number): void {
    if (newIndex !== this.cursorIndex){
      this.cursorIndex = newIndex;
      this.fireEvent(CustomEventType.CursorChanged, { index: newIndex });
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
      } else if (evt.key === "Enter") {
        this.handleSelection();
      }

      if(evt.key === "Escape") {
        this.fireEvent(CustomEventType.Canceled);
      }
    }
  }

  private calculatePosition() {
    const optionsBounds = this.shadowRoot.querySelector('.options').getBoundingClientRect();
    if (this.anchorTo) {
      const anchorBounds = this.anchorTo.getBoundingClientRect();    
      const topTop = anchorBounds.top - optionsBounds.height;
      if (topTop > 0 && anchorBounds.bottom + optionsBounds.height > window.innerHeight) {
        this.top = topTop + window.pageYOffset;
      } else {
        this.top = anchorBounds.bottom + window.pageYOffset;
      }
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
    return html`
      <style>
        .options {
          top: ${this.top}px;
          width: ${this.width}px;
        }
      </style>
      <div class="options ${this.visible ? "show": ""}">
        ${this.options.map((option: any, index: number)=>html`
          <div 
            @mousemove=${()=>{this.setCursor(index)}}
            @click=${()=>{this.handleSelection();}}
            class="option ${index == this.cursorIndex ? 'focused' : ''}">
              ${renderOption(option, index == this.cursorIndex)}
          </div>
        `)}
      </div>`;
  }
}
