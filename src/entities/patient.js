import { create, get, list, logAccess, remove, update } from './storage.js';
export const all = () => list('patients');
export const find = (id) => get('patients', id);
export const save = (data) => data.id ? update('patients', data.id, data) : create('patients', data);
export const destroy = (id) => { logAccess(id, 'DELETE_PATIENT', 'Exclusão cadastral solicitada'); remove('patients', id); };
