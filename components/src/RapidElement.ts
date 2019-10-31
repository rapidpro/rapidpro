import { LitElement } from 'lit-element';
import { CustomEventType } from './interfaces';

export interface EventHandler {
  event: string;
  method: EventListener;
  isDocument?: boolean;
}

export default class RapidElement extends LitElement {

  public getEventHandlers(): EventHandler[] {
    return [];
  }

  connectedCallback() {
    super.connectedCallback();
    for (const handler of this.getEventHandlers()) {
      if (handler.isDocument) {
        document.addEventListener(handler.event, handler.method.bind(this));
      } else {
        this.addEventListener(handler.event, handler.method.bind(this));
      }
    }
  }

  disconnectedCallback() {
    for (const handler of this.getEventHandlers()) {
      if (handler.isDocument) {
        document.removeEventListener(handler.event, handler.method);
      } else {
        this.removeEventListener(handler.event, handler.method);
      }
    }
    super.disconnectedCallback();
  }

  public fireEvent(type: string): void {
    this.dispatchEvent(new Event(type, {
      bubbles: true,
      composed: true
  }))
  }

  public fireCustomEvent(type: CustomEventType, detail: any = {}): void {
    const event = new CustomEvent(type, {
        detail,
        bubbles: true,
        composed: true
    });
    this.dispatchEvent(event);
  };
}