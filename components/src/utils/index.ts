import axios, { AxiosResponse, CancelToken, AxiosRequestConfig } from 'axios';
import { html, TemplateResult } from 'lit-html';

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

export const getUrl = (
    url: string,
    cancelToken: CancelToken = null
): Promise<AxiosResponse> => {
    const csrf = getCookie('csrftoken');
    const headers = csrf ? { 'X-CSRFToken': csrf } : {};
    const config: AxiosRequestConfig = { headers };
    if (cancelToken) {
        config.cancelToken = cancelToken;
    }
    return axios.get(url, config);
};

export const postUrl = (url: string, payload: any): Promise<AxiosResponse> => {
    const csrf = getCookie('csrftoken');
    const headers = csrf ? { 'X-CSRFToken': csrf } : {};
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
