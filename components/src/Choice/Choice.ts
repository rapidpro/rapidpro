import { customElement, TemplateResult, html, css, property } from 'lit-element';
import { getUrl } from '../utils';
import axios, { AxiosResponse, CancelTokenSource } from 'axios';
import '../Options/Options';
import RapidElement, { EventHandler } from '../RapidElement';
import { CustomEventType } from '../interfaces';

const LOOK_AHEAD = 20;

@customElement("rp-choice")
export default class Choice extends RapidElement {

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
    `
  }

  @property({type: Array})
  selected: any[] = [];

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

  @property({type: Boolean})
  fetching: boolean;

  @property({attribute: false})
  cursorIndex: number;

  @property({attribute: false})
  anchorElement: HTMLElement;

  private lastQuery: number;
  private cancelToken: CancelTokenSource;
  private complete: boolean;
  private page: number;
  private query: string;

  public constructor() {
    super();
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

       // if our cursor changed, lets make sure our scrollbox is showing it
    if(changedProperties.has("cursorIndex")) {
      if (this.options.length > 0 && 
          this.query && 
          !this.complete && 
          this.cursorIndex > this.options.length - LOOK_AHEAD) {
        this.fetchOptions(this.query, this.page + 1);
      }
    }
  }

  private handleOptionSelection(event: CustomEvent) {
    const selected = event.detail.selected;
    this.selected = [selected];
    this.options = [];
    this.input = selected.name;
 }

  public fetchOptions(query: string, page: number = 0) {
    
    if (!this.fetching) {
      // make sure we cancel any previous request
      if (this.cancelToken) {
        this.cancelToken.cancel();
      }

      const CancelToken = axios.CancelToken;
      this.cancelToken = CancelToken.source();

      this.fetching = true;
      getUrl(this.endpoint + encodeURIComponent(query) + "&page=" + page, this.cancelToken.token).then((response: AxiosResponse) => {
        if (page === 0) {
          this.options = response.data.filter((option: any) => option.level > 0);
          this.cursorIndex = 0;
          this.query = query;
          this.complete = this.options.length === 0;
        } else {
          const newResults = response.data.filter((option: any) => option.level > 0);
          if (newResults.length > 0) {
            this.options = [ ...this.options, ...newResults];
          }
          this.complete = newResults.length === 0
        }
        this.fetching = false;
        this.page = page;
      }).catch((reason: any)=>{
        // cancelled
      });
    }
  }

  private handleBlur() {
    // we don't do this immediately so we can handle click events outside of our input
    window.setTimeout(()=>{this.options = []}, 100);
  }

  private handleFocus(): void {
  }

  private handleKeyUp(evt: KeyboardEvent) {
    const ele = evt.currentTarget as HTMLInputElement;
    this.input = ele.value.trim();
  }

  private handleCancel() {
    this.options = [];
  }

  private handleCursorChanged(event: CustomEvent) {
    this.cursorIndex = event.detail.index;
  }

  public getEventHandlers(): EventHandler[] {
    return [
      { event: CustomEventType.Canceled, method: this.handleCancel },
      { event: CustomEventType.CursorChanged, method: this.handleCursorChanged },
      { event: CustomEventType.Selection, method: this.handleOptionSelection },
    ];
  }

  public firstUpdated(changedProperties: any) {
    this.anchorElement = this.shadowRoot.querySelector(".input-container")
  }

  public render(): TemplateResult {
    return html`
      <div class="container">
        <div class="input-container" @click=${()=>{ this.shadowRoot.querySelector("input").focus()}}>
          <input 
            @keyup=${this.handleKeyUp}
            @blur=${this.handleBlur} 
            @focus=${this.handleFocus} 
            type="text" 
            .value=${this.input}  
            placeholder="${this.placeholder}">
        </div>
        <rp-options
          cursorIndex=${this.cursorIndex}
          .anchorTo=${this.anchorElement}
          .options=${this.options}
          ?visible=${this.input.length > 0 && this.options.length > 0}
        ></rp-options>
      </div>
    `
  }
}