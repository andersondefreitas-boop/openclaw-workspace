import { list, logAccess } from './storage.js';
export const all = () => list('accessLogs');
export const record = logAccess;
