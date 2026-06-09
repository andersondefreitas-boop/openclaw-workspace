const KEY_PREFIX = 'anestesiaapp:';
const now = () => new Date().toISOString();
const uid = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

const defaults = {
  patients: [
    {
      id: 'pat-ana-souza', name: 'Ana Souza', cpf: '123.456.789-00', birthDate: '1979-04-12', sex: 'Feminino',
      phone: '(47) 99999-0101', email: 'ana.souza@example.com', surgeon: 'Dra. Helena Costa', hospital: 'SEAMEP',
      emergencyContact: 'João Souza - (47) 98888-0101', lgpdConsent: true, notes: 'Hipertensão controlada.', createdAt: now(), updatedAt: now()
    },
    {
      id: 'pat-carlos-lima', name: 'Carlos Lima', cpf: '987.654.321-00', birthDate: '1964-11-03', sex: 'Masculino',
      phone: '(47) 99999-0202', email: 'carlos.lima@example.com', surgeon: 'Dr. Marcos Farias', hospital: 'HRAVA',
      emergencyContact: 'Maria Lima - (47) 97777-0202', lgpdConsent: true, notes: 'DM2 em uso de metformina.', createdAt: now(), updatedAt: now()
    }
  ],
  appointments: [
    { id: 'apt-001', patientId: 'pat-ana-souza', anesthesiologist: 'Dr. Anderson', date: new Date().toISOString().slice(0, 10), time: '08:30', procedure: 'Colecistectomia videolaparoscópica', hospital: 'SEAMEP', status: 'Agendada', surgeon: 'Dra. Helena Costa', createdAt: now(), updatedAt: now() },
    { id: 'apt-002', patientId: 'pat-carlos-lima', anesthesiologist: 'Dr. Anderson', date: new Date(Date.now() + 86400000).toISOString().slice(0, 10), time: '10:00', procedure: 'Herniorrafia inguinal', hospital: 'HRAVA', status: 'Pendente exames', surgeon: 'Dr. Marcos Farias', createdAt: now(), updatedAt: now() }
  ],
  consultations: [],
  accessLogs: []
};

function readCollection(name) {
  const raw = localStorage.getItem(KEY_PREFIX + name);
  if (!raw) {
    localStorage.setItem(KEY_PREFIX + name, JSON.stringify(defaults[name] || []));
    return defaults[name] || [];
  }
  try { return JSON.parse(raw); } catch { return []; }
}

function writeCollection(name, records) {
  localStorage.setItem(KEY_PREFIX + name, JSON.stringify(records));
  window.dispatchEvent(new Event('anestesiaapp:data'));
}

export function list(name) { return readCollection(name).sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || '')); }
export function get(name, id) { return readCollection(name).find((record) => record.id === id) || null; }
export function create(name, data) {
  const record = { ...data, id: data.id || uid(), createdAt: data.createdAt || now(), updatedAt: now() };
  writeCollection(name, [record, ...readCollection(name)]);
  return record;
}
export function update(name, id, data) {
  let saved = null;
  const records = readCollection(name).map((record) => {
    if (record.id !== id) return record;
    saved = { ...record, ...data, id, updatedAt: now() };
    return saved;
  });
  writeCollection(name, records);
  return saved;
}
export function remove(name, id) { writeCollection(name, readCollection(name).filter((record) => record.id !== id)); }
export function upsertBy(name, predicate, data) {
  const found = readCollection(name).find(predicate);
  return found ? update(name, found.id, data) : create(name, data);
}
export function logAccess(patientId, action, details = '') { return create('accessLogs', { patientId, action, details, user: 'Dr. Anderson', occurredAt: now() }); }
export const todayISO = () => new Date().toISOString().slice(0, 10);
export const formatDate = (iso) => iso ? new Date(`${iso}T00:00:00`).toLocaleDateString('pt-BR') : '-';
export const ageFromBirth = (birthDate) => {
  if (!birthDate) return '';
  const diff = Date.now() - new Date(`${birthDate}T00:00:00`).getTime();
  return Math.abs(new Date(diff).getUTCFullYear() - 1970);
};
