import axios, { AxiosResponse, CancelToken, AxiosRequestConfig } from 'axios';
import { html, TemplateResult } from 'lit-html';
const dynamicTemplate = require('es6-dynamic-template');

export interface Asset {
    key?: string;
}

interface AssetPage {
    assets: Asset[];
    next: string;
}

/** Get the value for a named cookie */
export const getCookie = (name: string): string => {
    for (const cookie of document.cookie.split(';')) {
        const idx = cookie.indexOf('=');
        let key = cookie.substr(0, idx);
        let value = cookie.substr(idx + 1);

        // no spaces allowed
        key = key.trim();
        value = value.trim();

        if (key === name) {
            return value;
        }
    }
    return null;
};

export type ClassMap = {
    [className: string]: boolean
};

export const getClasses = (map: ClassMap): string => {
    const classNames: string[] = [];
    Object.keys(map).forEach((className: string) => {
        if (map[className]) {
            classNames.push(className);
        }
    });

    let result = classNames.join(' ');
    if (result.trim().length > 0) {
        result = ' ' + result;
    }
    return result;
};

export const getAssetPage = (url: string): Promise<AssetPage> => {
    return new Promise<AssetPage>((resolve, reject) => {
        getUrl(url)
            .then((response: AxiosResponse) => {
                resolve({
                    assets: response.data.results,
                    next: response.data.next
                });
            })
            .catch(error => reject(error));
    });
};

export const getAssets = async (url: string): Promise<Asset[]> => {
    if (!url) {
        return new Promise<Asset[]>((resolve, reject) => resolve([]));
    }

    let assets: Asset[] = [];
    let pageUrl = url;
    while (pageUrl) {
        const assetPage = await getAssetPage(pageUrl);
        assets = assets.concat(assetPage.assets);
        pageUrl = assetPage.next;
    }
    return assets;
};

export const getUrl = (
    url: string,
    cancelToken: CancelToken = null,
    pjax: boolean = false
): Promise<AxiosResponse> => {
    const csrf = getCookie('csrftoken');
    const headers: any = csrf ? { 'X-CSRFToken': csrf } : {};

    if (pjax) {
        headers['X-PJAX'] = 'true';
    }

    const config: AxiosRequestConfig = { headers };
    if (cancelToken) {
        config.cancelToken = cancelToken;
    }
    return axios.get(url, config);
};

export const postUrl = (
    url: string,
    payload: any,
    pjax: boolean = false
): Promise<AxiosResponse> => {
    const csrf = getCookie('csrftoken');
    const headers: any = csrf ? { 'X-CSRFToken': csrf } : {};

    if (pjax) {
        headers['X-PJAX'] = 'true';
    }

    return axios.post(url, payload, { headers });
};

/**
 */
export const renderIf = (predicate: boolean | any) => (
    then: () => TemplateResult,
    otherwise?: () => TemplateResult
) => {
    return predicate ? then() : otherwise ? otherwise() : html``;
};

export const hexToRgb = (hex: string): { r: number, g: number, b: number } => {
    var result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result
        ? {
              r: parseInt(result[1], 16),
              g: parseInt(result[2], 16),
              b: parseInt(result[3], 16)
          }
        : null;
};

export const getElementOffset = (
    ele: HTMLElement
): {
    top: number,
    left: number,
    bottom: number,
    right: number,
    width: number,
    height: number
} => {
    const rect = ele.getBoundingClientRect();
    const scrollLeft =
        window.pageXOffset || document.documentElement.scrollLeft;
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    return {
        top: rect.top + scrollTop,
        left: rect.left + scrollLeft,
        bottom: rect.top + rect.height,
        right: rect.left + rect.width,
        width: rect.width,
        height: rect.height
    };
};

export const plural = (count: number, singular: string, plural: string) => {
    return count === 1 ? singular : plural;
};

export const range = (start: number, end: number) =>
    Array.from({ length: end - start }, (v: number, k: number) => k + start);

export const fillTemplate = (
    template: string,
    replacements: { [key: string]: string | number }
): TemplateResult => {
    for (const key in replacements) {
        const className = key + '-replaced';
        replacements[
            key
        ] = `<span class="${className}">${replacements[key]}</span>`;
    }

    const templateDiv = document.createElement('div');
    templateDiv.innerHTML = dynamicTemplate(template, replacements);
    return html`
        ${templateDiv}
    `;
};

/*!
 * Serialize all form data into a query string
 * (c) 2018 Chris Ferdinandi, MIT License, https://gomakethings.com
 * @param  {Node}   form The form to serialize
 * @return {String}      The serialized form data
 */
export const serialize = function(form: any) {
    // Setup our serialized data
    const serialized = [];

    // Loop through each field in the form
    for (let i = 0; i < form.elements.length; i++) {
        const field = form.elements[i];

        // Don't serialize fields without a name, submits, buttons, file and reset inputs, and disabled fields
        if (
            !field.name ||
            field.disabled ||
            field.type === 'file' ||
            field.type === 'reset' ||
            field.type === 'submit' ||
            field.type === 'button'
        )
            continue;

        // If a multi-select, get all selections
        if (field.type === 'select-multiple') {
            for (var n = 0; n < field.options.length; n++) {
                if (!field.options[n].selected) continue;
                serialized.push(
                    encodeURIComponent(field.name) +
                        '=' +
                        encodeURIComponent(field.options[n].value)
                );
            }
        }

        // Convert field data to a query string
        else if (
            (field.type !== 'checkbox' && field.type !== 'radio') ||
            field.checked
        ) {
            serialized.push(
                encodeURIComponent(field.name) +
                    '=' +
                    encodeURIComponent(field.value)
            );
        }
    }

    return serialized.join('&');
};
