import { create, get, list, update, remove } from './storage.js';
export const all = () => list('appointments');
export const find = (id) => get('appointments', id);
export const save = (data) => data.id ? update('appointments', data.id, data) : create('appointments', data);
export const destroy = (id) => remove('appointments', id);
