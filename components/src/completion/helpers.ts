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

export interface Position {
    top: number;
    left: number;
}

export type KeyedAssets = { [assetType: string]: string[] };

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

/**
 * Takes a dot query and returns the completions options at the current level
 * @param dotQuery query such as "contact.first_n"
 */
export const getCompletions = (
    schema: CompletionSchema,
    dotQuery: string,
    keyedAssets: KeyedAssets = {}
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
                } else if (nextType && nextType.property_template) {
                    prefix += part + '.';
                    const template = nextType.property_template;
                    if (keyedAssets[nextType.name]) {
                        currentProps = keyedAssets[nextType.name].map(
                            (key: string) => ({
                                key: template.key.replace('{key}', key),
                                help: template.help.replace('{key}', key),
                                type: template.type
                            })
                        );
                    } else {
                        currentProps = [];
                    }
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

export const getOffset = (el: HTMLElement) => {
    var rect = el.getBoundingClientRect(),
        scrollLeft = window.pageXOffset || document.documentElement.scrollLeft,
        scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    return { top: rect.top + scrollTop, left: rect.left + scrollLeft };
};

export const getVerticalScroll = (ele: Node) => {
    let current = ele;
    let verticalScroll = 0;
    while (current) {
        current = current.parentNode;
    }
    return verticalScroll;
};

export const getCompletionName = (option: CompletionOption): string => {
    return (
        option.name || option.signature.substr(0, option.signature.indexOf('('))
    );
};

export const getCompletionSignature = (option: CompletionOption): string => {
    return option.signature.substr(option.signature.indexOf('('));
};
