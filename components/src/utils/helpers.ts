import { Contact } from '../interfaces';

export const getDisplayURN = (contact: Contact, scheme: string = null) => {
    if (contact.urns.length > 0) {
        return contact.urns[0].path;
    }
    return '';
};
