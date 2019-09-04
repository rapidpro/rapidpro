import { customElement, TemplateResult, html, css, property } from 'lit-element';
import RapidElement, { EventHandler } from '../RapidElement';
import ExcellentParser, { Expression } from './ExcellentParser';
import TextInput from '../textinput/TextInput';
import { getCompletions, CompletionSchema, getFunctions, getCursorPosition, Position } from './helpers';
import { getUrl } from '../utils';
import { AxiosResponse } from 'axios';
import getCaretCoordinates from 'textarea-caret';
import { directive, Part} from 'lit-html';
import {unsafeHTML} from 'lit-html/directives/unsafe-html.js';

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

      #anchor {
        position: absolute;
        visibility: visible;
        width: 250px;
        border: 0px solid purple;
      }

      .fn-marker {
        font-weight: bold;
        font-size: 42px;
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
  options: any[] = []; // [{ name: "boom", detail: "blerp"}];

  @property({type: String})
  value: string = "";

  @property({type: String})
  completionsEndpoint: string;

  @property({type: String})
  functionsEndpoint: string;

  @property({type: Boolean})
  textarea: boolean;

  private inputElement: HTMLInputElement;
  private query: string;
  
  public firstUpdated(changedProperties: Map<string, any>) {
    this.textInputElement = this.shadowRoot.querySelector("rp-textinput") as TextInput;
    this.anchorElement = this.shadowRoot.querySelector("#anchor");
    
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

    if (this.schema) {
      const cursor = ele.inputElement.selectionStart;
      const input = ele.inputElement.value.trim().substring(0, cursor);
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
              left: caret.left + 5, 
              top: ele.inputElement.offsetTop + caret.top - ele.inputElement.scrollTop  + 20}

            this.query = currentExpression.text.substr(i, currentExpression.text.length - i);
            this.options = [
              ...getCompletions(this.schema, this.query), 
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

  private handleInput(evt: KeyboardEvent) {
    const ele = evt.currentTarget as TextInput;
    this.executeQuery(ele);
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
      
      // strip out our query
      value = value.substr(0, value.lastIndexOf(this.query));
    
      // now add on our selection
      value += insertText;

      this.inputElement.value = value;
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
    return html`
      <style>
        #anchor {
          top:${this.anchorPosition.top}px;
          left:${this.anchorPosition.left}px;
        }
      </style>
      <div id="anchor"></div>
      <rp-textinput 
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
        .anchorTo=${this.anchorElement}
        .options=${this.options}
        .renderOption=${this.renderCompletionOption}
        ?visible=${this.options.length > 0}
      ></rp-options>
    `;
  }
}