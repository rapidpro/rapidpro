import { customElement, TemplateResult, html, css, property } from 'lit-element';
import RapidElement, { EventHandler } from '../RapidElement';
import ExcellentParser, { Expression } from './ExcellentParser';
import TextInput from '../textinput/TextInput';
import { getCompletions, CompletionSchema, getFunctions, Position, KeyedAssets, getVerticalScroll, getOffset } from './helpers';
import { getUrl, getAssets, Asset } from '../utils';
import { AxiosResponse } from 'axios';
import getCaretCoordinates from 'textarea-caret';
import { directive, Part } from 'lit-html';
import { unsafeHTML } from 'lit-html/directives/unsafe-html.js';
import { styleMap } from 'lit-html/directives/style-map.js';

const marked = require('marked');

export interface FunctionExample {
  template: string;
  output: string;
}

export interface CompletionOption {
  name?: string;
  summary: string;

  // functions
  signature?: string;
  detail?: string;
  examples?: FunctionExample[];
}

const markedRender = directive((contents: string) => (part: Part) => { 
  part.setValue(unsafeHTML(marked(contents)))
});


/**
 * Completion is a text input that handles excellent completion options in a popup
 */
@customElement("rp-completion")
export default class Completion extends RapidElement {
  static get styles() {
    return css`
      
      :host {
        display: block;
      }

      rp-options {
        --widget-box-shadow-focused: 0 0 4px rgba(0, 0, 0, 0.15);
        --color-focus: #e6e6e6;
      }

      .comp-container {
        position: relative;
        height: 100%;
      }

      #anchor {
        /* background: rgba(132, 40, 158, .1); */
        position: absolute;
        visibility: hidden;
        width: 250px;
        height: 20px;
      }

      .fn-marker {
        font-weight: bold;
        font-size: 42px;
      }

      .option-slot {
        background: #fff;
      }

      .current-fn {
        padding: 10px;
        margin: 5px;
        background: var(--color-primary-light);
        color: rgba(0, 0, 0, .5);
        border-radius: var(--curvature-widget);
        font-size: 90%;
      }

      .footer {
        padding: 5px 10px;
        background: var(--color-primary-light);
        color: rgba(0, 0, 0, .5);
        font-size: 80%;
        border-bottom-left-radius: var(--curvature-widget);
        border-bottom-right-radius: var(--curvature-widget);
      }

      code {
        background: rgba(0,0,0,.1);
        padding: 1px 5px;
        border-radius: var(--curvature);
      }
    `
  }

  static parser = new ExcellentParser('@', [
    'contact',
    'fields',
    'urns',
  ]);

  /** Remote description of our completion schema */  
  private schema: CompletionSchema;

  /** Remote list of our function options */
  private functions: CompletionOption[];

  private keyedAssets: KeyedAssets;

  @property({ type: Object})
  anchorPosition: Position = { left: 0, top: 0};

  @property({attribute: false})
  currentFunction: CompletionOption;

  @property({type: String})
  placeholder: string = "";

  @property({attribute: false})
  textInputElement: TextInput;

  @property({attribute: false})
  anchorElement: HTMLDivElement;

  @property({type: Array})
  options: any[] = [];

  @property({type: String})
  name: string = "";

  @property({type: String})
  value: string = "";

  @property({type: String})
  completionsEndpoint: string;

  @property({type: String})
  functionsEndpoint: string;

  @property({type: String})
  fieldsEndpoint: string;

  @property({type: Boolean})
  textarea: boolean;

  private hiddenElement: HTMLInputElement;
  private inputElement: HTMLInputElement;
  private query: string;
  
  public firstUpdated(changedProperties: Map<string, any>) {
    this.textInputElement = this.shadowRoot.querySelector("rp-textinput") as TextInput;
    this.anchorElement = this.shadowRoot.querySelector("#anchor");

    // TODO: fetch these once per page, not once per control
    if (this.completionsEndpoint) {
      getUrl(this.completionsEndpoint).then((response: AxiosResponse) => {
        this.schema = response.data as CompletionSchema;
      });
    }

    if (this.functionsEndpoint) {
      getUrl(this.functionsEndpoint).then((response: AxiosResponse) => {
        this.functions = response.data as CompletionOption[];
      });
    }

    if (this.fieldsEndpoint) {
      getAssets(this.fieldsEndpoint).then((assets: Asset[])=>{
        this.keyedAssets = { fields: assets.map((asset: Asset)=> asset.key ) }
      });      
    }

    // create our hidden container so it gets included in our host element's form
    this.hiddenElement = document.createElement("input");
    this.hiddenElement.setAttribute("type", "hidden");
    this.hiddenElement.setAttribute("name", this.getAttribute("name"));
    this.hiddenElement.setAttribute("value", this.getAttribute("value") || "");
    this.appendChild(this.hiddenElement);
  }

  private handleKeyUp(evt: KeyboardEvent) {

    // if we have options, ignore keys that are meant for them
    if (this.options.length > 0) {

      if(evt.key === "ArrowUp" || evt.key === "ArrowDown") {
        return;
      }

      if (evt.ctrlKey) {
        if (evt.key === "n" || evt.key === "p") {
          return;
        }
      }

      if(evt.key === "Enter" || evt.key === "Escape" || evt.key === "Tab" || evt.key.startsWith("Control")) {
        return;
      }

      this.executeQuery(evt.currentTarget as TextInput);
    }
  }

  private handleClick(evt: MouseEvent) {
    this.executeQuery(evt.currentTarget as TextInput)
  }

  /**
   * handle the user moving the caret to a new location
   */
  private executeQuery(ele: TextInput) {
    this.inputElement = ele.inputElement;
    this.currentFunction = null;

    if (this.schema) {
      const cursor = ele.inputElement.selectionStart;
      const input = ele.inputElement.value.substring(0, cursor);
      const expressions = Completion.parser.findExpressions(input);
      const currentExpression = expressions.find((expr: Expression)=>expr.start <= cursor && (expr.end > cursor || expr.end === cursor && !expr.closed));

      if (currentExpression) {
        const includeFunctions = currentExpression.text.indexOf('(') > -1;
        if (includeFunctions) {
          const functionQuery = Completion.parser.functionContext(currentExpression.text);
          if (functionQuery) {
            const fns = getFunctions(this.functions, functionQuery);
            if (fns.length > 0) {
              this.currentFunction = fns[0];
            }
          }
        }
      
        for (let i = currentExpression.text.length; i >= 0; i--) {
          const curr = currentExpression.text[i];
          if (curr === '@' || curr === '(' || curr === ' ' || curr === ',' || curr === ')' || i === 0) {
            // don't include non-expression chars
            if (curr === '(' || curr === ' ' || curr === ',' || curr === ')' || curr === '@') {
              i++;
            }

            var caret = getCaretCoordinates(ele.inputElement, currentExpression.start + i);
            this.anchorPosition = {
               left: caret.left - 2 - this.inputElement.scrollLeft,
               top: caret.top - this.inputElement.scrollTop
            }

            this.query = currentExpression.text.substr(i, currentExpression.text.length - i);
            this.options = [
              ...getCompletions(this.schema, this.query, this.keyedAssets),
              ...(includeFunctions ? getFunctions(this.functions, this.query) : [])
            ];

            return;
          }
        }
      } else {
        this.options = [];
        this.query = "";
      }
    }
  }
  
  public updated(changedProperties: Map<string, any>) {
    super.updated(changedProperties);

    // if our cursor changed, lets make sure our scrollbox is showing it
    if(changedProperties.has("value")) {
      this.hiddenElement.setAttribute("value", this.value);
    }
  }

  private handleInput(evt: KeyboardEvent) {
    const ele = evt.currentTarget as TextInput;
    this.executeQuery(ele);
    this.value = ele.inputElement.value;
  }

  private handleOptionCanceled(evt: CustomEvent) {
    this.options = [];
    this.query = "";
  }
  
  private handleOptionSelection(evt: CustomEvent) {
    const option = evt.detail.selected as CompletionOption;
    const tabbed = evt.detail.tabbed;

    let insertText = "";
    
    if (option.signature) {
      // they selected a function
      insertText = option.signature.substr(0, option.signature.indexOf("(") + 1);
    } else {
      insertText = option.name;
    }

    if (this.inputElement) {
      let value = this.inputElement.value;
      const insertionPoint = this.inputElement.selectionStart - this.query.length;
      
      // strip out our query
      // const insertionPoint = value.lastIndexOf(value.substring(0, this.inputElement.selectionStart));
      const leftSide = value.substr(0, insertionPoint);
      const remaining = value.substr(insertionPoint + this.query.length);
      const caret = leftSide.length + insertText.length;

      // set our value and our new caret
      this.inputElement.value = leftSide + insertText + remaining;
      this.inputElement.setSelectionRange(caret, caret);

      // now scroll our text box if necessary
      const position = getCaretCoordinates(this.inputElement, caret);
      if (position.left > this.inputElement.width) {
        this.inputElement.scrollLeft = position.left;
      }

    }

    this.query = "";
    this.options = [];
    
    if (tabbed) {
      this.executeQuery(this.textInputElement);
    }
  }

  private renderCompletionOption(option: CompletionOption, selected: boolean) {
    if(option.signature) {

      const argStart = option.signature.indexOf("(")
      const name = option.signature.substr(0, argStart);
      const args = option.signature.substr(argStart);

      return html`
        <div style="${selected ? 'font-weight: 400' : ''}">
          <div style="display:inline-block;">ƒ</div>
          <div style="display:inline-block">${name}</div>
          ${selected ? html`
            <div style="display:inline-block; font-weight: 300; font-size: 85%">${args}</div>
            <div class="detail">${markedRender(option.summary)}</div>
          ` : null}
        </div>`;
      }

    return html`
    <div>
      <div style="${selected ? 'font-weight: 400' : ''}">${option.name}</div>
      ${selected ? html`<div style="font-size: 85%">${option.summary}</div>` : null}
    </div>`
  }

  public render(): TemplateResult {

    const anchorStyles = { 
      top: `${this.anchorPosition.top}px`,
      left: `${this.anchorPosition.left}px`
    }

    return html`
      <div class="comp-container">
        <div id="anchor" style=${styleMap(anchorStyles)}></div> 
        <rp-textinput 
          name=${this.name}
          placeholder=${this.placeholder}
          @keyup=${this.handleKeyUp}
          @click=${this.handleClick}
          @input=${this.handleInput}
          .value=${this.value}
          ?textarea=${this.textarea}
          >
        </rp-textinput>
        <rp-options
          @rp-selection=${this.handleOptionSelection}
          @rp-canceled=${this.handleOptionCanceled}
          .anchorTo=${this.anchorElement}
          .options=${this.options}
          .renderOption=${this.renderCompletionOption}
          ?visible=${this.options.length > 0}
        >
          ${this.currentFunction ? html`<div class="current-fn">${this.renderCompletionOption(this.currentFunction, true)}</div>`: null}
          <div class="footer">Tab to complete, enter to select</div>
        </rp-options>
      </div>
    `;
  }
}