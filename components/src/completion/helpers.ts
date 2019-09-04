import { CompletionOption } from './Completion';

export interface CompletionProperty {
    key: string;
    help: string;
    type: string;
}

export interface CompletionType {
    name: string;

    key_source?: string;
    property_template?: CompletionProperty;
    properties?: CompletionProperty[];
}

export interface CompletionSchema {
    types: CompletionType[];
    root: CompletionProperty[];
    root_no_session: CompletionProperty[];
}

export const getFunctions = (
    functions: CompletionOption[],
    query: string
): CompletionOption[] => {
    if (!query) {
        return functions;
    }
    return functions.filter((option: CompletionOption) => {
        if (option.signature) {
            return option.signature.indexOf(query) === 0;
        }
        return false;
    });
};

export interface Position {
    top: number;
    left: number;
}

/**
 * Takes a dot query and returns the completions options at the current level
 * @param dotQuery query such as "contact.first_n"
 */
export const getCompletions = (
    schema: CompletionSchema,
    dotQuery: string
): CompletionOption[] => {
    const parts = (dotQuery || '').split('.');
    let currentProps: CompletionProperty[] = schema.root_no_session;

    let prefix = '';
    let part = '';
    while (parts.length > 0) {
        part = parts.shift();
        if (part) {
            // eslint-disable-next-line
            const nextProp = currentProps.find(
                (prop: CompletionProperty) => prop.key === part
            );
            if (nextProp) {
                // eslint-disable-next-line
                const nextType = schema.types.find(
                    (type: CompletionType) => type.name === nextProp.type
                );
                if (nextType && nextType.properties) {
                    currentProps = nextType.properties;
                    prefix += part + '.';
                } else {
                    // eslint-disable-next-line
                    currentProps = currentProps.filter(
                        (prop: CompletionProperty) =>
                            prop.key.startsWith(part.toLowerCase())
                    );
                    break;
                }
            } else {
                // eslint-disable-next-line
                currentProps = currentProps.filter((prop: CompletionProperty) =>
                    prop.key.startsWith(part.toLowerCase())
                );
                break;
            }
        }
    }

    return currentProps.map((prop: CompletionProperty) => {
        const name =
            prop.key === '__default__'
                ? prefix.substr(0, prefix.length - 1)
                : prefix + prop.key;
        return { name, summary: prop.help };
    });
};

export const getCompletionName = (option: CompletionOption): string => {
    return (
        option.name || option.signature.substr(0, option.signature.indexOf('('))
    );
};

export const getCompletionSignature = (option: CompletionOption): string => {
    return option.signature.substr(option.signature.indexOf('('));
};

/**
 * returns x, y coordinates for absolute positioning of a span within a given text input
 * at a given selection point
 * @param {object} input - the input element to obtain coordinates for
 * @param {number} selectionPoint - the selection point for the input
 */
export const getCursorPosition = (input: any, selectionPoint: any): Position => {
    const {
      offsetLeft: inputX,
      offsetTop: inputY,
    } = input
    // create a dummy element that will be a clone of our input
    const div = document.createElement('div')
    // get the computed style of the input and clone it onto the dummy element
    const copyStyle = getComputedStyle(input)
    for (const prop of copyStyle) {
      div.style[prop as any] = copyStyle[prop as any]
    }
    // we need a character that will replace whitespace when filling our dummy element if it's a single line <input/>
    const swap = '.'
    const inputValue = input.tagName === 'INPUT' ? input.value.replace(/ /g, swap) : input.value
    // set the div content to that of the textarea up until selection
    const textContent = inputValue.substr(0, selectionPoint)
    // set the text content of the dummy element div
    div.textContent = textContent
    if (input.tagName === 'TEXTAREA') div.style.height = 'auto'
    // if a single line input then the div needs to be single line and not break out like a text area
    if (input.tagName === 'INPUT') div.style.width = 'auto'
    // create a marker element to obtain caret position
    const span = document.createElement('span')
    // give the span the textContent of remaining content so that the recreated dummy element is as close as possible
    span.textContent = inputValue.substr(selectionPoint) || '.'
    // append the span marker to the div
    div.appendChild(span)
    // append the dummy element to the body
    document.body.appendChild(div)
    // get the marker position, this is the caret position top and left relative to the input
    const { offsetLeft: spanX, offsetTop: spanY } = span
    // lastly, remove that dummy element
    // NOTE:: can comment this out for debugging purposes if you want to see where that span is rendered
    document.body.removeChild(div)
    // return an object with the x and y of the caret. account for input positioning so that you don't need to wrap the input
    return {
      left: inputX + spanX,
      top: inputY + spanY,
    }
  }