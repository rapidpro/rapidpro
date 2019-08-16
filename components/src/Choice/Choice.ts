import { LitElement, customElement, TemplateResult, html, css, property } from 'lit-element';
import { getUrl } from '../utils';
import axios, { AxiosResponse, CancelTokenSource } from 'axios';

@customElement("rp-choice")
export default class Choice extends LitElement {

  static get styles() {
    return css`
      textarea, input {
        border: 0;
        width: 100%;
        height: 100%;
        margin: 0;
        background: var(--color-widget-bg);
        color: var(--color-text);
        font-size: 16px;
        cursor: pointer;
        transition: all ease-in-out 200ms;
      }

      textarea:focus, input:focus {
        outline: none;
        cursor: text;
      }

      .container {
        display: flex;
        flex-direction: column;
        border: 0px solid green;
      }

      .input-container {
        padding: 8px 8px;
        border: 1px solid transparent;
        border-radius: 5px;
        overflow: hidden;
        background: var(--color-widget-bg);
        cursor: pointer;
        transition: all ease-in-out 200ms;
      }

      .input-container:hover {
        background: var(--color-widget-hover);
      }

      .input-container:hover input {
        background: var(--color-widget-hover);
      }

      .options {
        overflow-y: scroll;
        background: #fff;
        border-radius: 5px;
        visibility: hidden;
        position: absolute;
        border: 1px solid var(--color-borders);
        box-shadow: 0px 0px 6px 1px rgba(0,0,0,.08);
        transition: opacity ease-in-out 150ms;
        opacity: 0;
        max-height: 300px;
      }

      .show {
        visibility: visible;
        opacity: 1;
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

  @property({attribute: false})
  renderOption: (option: any, selected: boolean) => void = this.renderOptionDefault;

  @property({attribute: false})
  renderOptionName: (option: any, selected: boolean) => void = this.renderOptionNameDefault;

  @property({attribute: false})
  renderOptionDetail: (option: any, selected: boolean) => void = this.renderOptionDetailDefault;

  @property({type: Array})
  selected: any[] = [];

  @property({type: Number})
  cursorIndex: number = 0;

  @property()
  placeholder: string = '';

  @property()
  endpoint: string;

  @property({type: String})
  input: string = '';

  @property({type: Array})
  options: any[] = [];

  @property({type: Number})
  quietMillis: number = 200;

  @property({type: Number})
  optionsTop: number;

  @property({type: Number})
  optionsWidth: number;

  @property({type: Number})
  optionsHeight: number;

  private lastQuery: number;
  private cancelToken: CancelTokenSource;

  public constructor() {
    super();
    document.addEventListener("scroll", ()=>{
      this.calculateOptionsPosition();
    });
  }

  private calculateOptionsPosition() {
    const optionsHeight = this.shadowRoot.querySelector(".options").getBoundingClientRect().height
    const input = this.shadowRoot.querySelector(".input-container");
    const bounds = input.getBoundingClientRect();
    const space = (window.innerHeight - bounds.bottom);
    if (space > optionsHeight) {
      this.optionsTop = bounds.bottom + window.scrollY + 1;
    } else {
      this.optionsTop = bounds.top - optionsHeight + window.scrollY - 1;
    }

    this.optionsWidth = bounds.width - 2;
  }

  public updated(changedProperties: Map<string, any>) {
    super.updated(changedProperties);
    if (changedProperties.has("selected")) {
      this.input = "";
      this.shadowRoot.querySelector("input").blur();
    }
    
    if (changedProperties.has("input") && !changedProperties.has("selected")) {
      if (!this.input) {
        this.options = [];
        return;
      }

      if (this.lastQuery) {
        window.clearTimeout(this.lastQuery);
      }
      this.lastQuery = window.setTimeout(()=>{
        this.fetchOptions(this.input);
      }, this.quietMillis);
    }

    if(changedProperties.has("options")) {
      this.calculateOptionsPosition();
    }

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
  }

  private moveCursor(direction: number): void {
    this.cursorIndex = Math.max(Math.min(this.cursorIndex + direction, this.options.length - 1), 0);
  }

  private handleSelection() {
    const selected = this.options[this.cursorIndex];

    this.selected = [selected];
    this.options = [];
    this.input = selected.name;
    
    const selectionEvent = new CustomEvent('rp-choice-selected', { 
      detail: this.selected,
      bubbles: true, 
      composed: true });
    this.dispatchEvent(selectionEvent);
  }

  private handleKeyDown(evt: KeyboardEvent) {
    if ((evt.ctrlKey && evt.key === "n") || evt.key === "ArrowDown") {
      this.moveCursor(1);
      evt.preventDefault();
    } else if ((evt.ctrlKey && evt.key === "p") || evt.key === "ArrowUp") {
      this.moveCursor(-1);
      evt.preventDefault();
    } else if (evt.key === "Enter") {
      this.handleSelection();
    }
  }

  private handleKeyUp(evt: KeyboardEvent) {
    const ele = evt.currentTarget as HTMLInputElement;
    this.input = ele.value.trim();
  }

  public fetchOptions(query: string) {
    
    // make sure we cancel any previous request
    if (this.cancelToken) {
      this.cancelToken.cancel();
    }

    const CancelToken = axios.CancelToken;
    this.cancelToken = CancelToken.source();

    getUrl(this.endpoint + encodeURIComponent(query), this.cancelToken.token).then((response: AxiosResponse) => {
      this.options = response.data.filter((option: any) => option.level > 0);
      this.cursorIndex = 0;
    }).catch((reason: any)=>{
      // cancelled
    });
  }

  private handleBlur() {
    // we don't do this immediately so we can handle click events outside of our input
    window.setTimeout(()=>{this.options = []}, 100);
  }

  private handleFocus(): void {
  }

  private renderOptionDefault(option: any, selected: boolean): TemplateResult {
    if (selected) {
      return html`<div class="name">${this.renderOptionName(option, selected)}</div><div class="detail">${this.renderOptionDetail(option, selected)}</div>`;
    } else {
      return html`<div class="name">${this.renderOptionName(option, selected)}</div>`;
    }
  }

  private renderOptionNameDefault(option: any, selected: boolean): TemplateResult {
    return html`${option.name}`
  }

  private renderOptionDetailDefault(option: any, selected: boolean): TemplateResult {
    return html`${option.detail}`
  }


  public render(): TemplateResult {
    return html`
      <style>
        .options {
          top: ${this.optionsTop}px;
          width: ${this.optionsWidth}px;
        }
      </style>
      <div class="container">
        <div class="input-container" @click=${()=>{ this.shadowRoot.querySelector("input").focus()}}>
          <input 
            @keydown=${this.handleKeyDown}
            @keyup=${this.handleKeyUp}
            @blur=${this.handleBlur} 
            @focus=${this.handleFocus} 
            type="text" 
            .value=${this.input}  
            placeholder="${this.placeholder}">
        </div>
        <div class="options ${this.input.length > 0 && this.options.length > 0 ? "show": ""}">
          ${this.options.map((option: any, index: number)=>html`
            <div 
              @mousemove=${(evt: MouseEvent)=>{this.cursorIndex = index}}
              @click=${()=>{this.handleSelection();}}
              class="option ${index == this.cursorIndex ? 'focused' : ''}">
                ${this.renderOption(option, index == this.cursorIndex)}
              </div>
          `)}
        </div>
      </div>
    `
  }
}