import { get, list, update, upsertBy } from './storage.js';
export const emptyConsultation = (appointmentId) => ({
  appointmentId, status: 'Rascunho', asa: 'II', weight: '', height: '', imc: '', bloodPressure: '', heartRate: '', spo2: '', mets: '',
  comorbidities: [], surgicalHistory: '', anestheticHistory: '', smoking: '', alcohol: '', drugs: '', physicalExam: '',
  mallampati: '', mouthOpening: '', cervicalMobility: '', thyromentalDistance: '', sternomentalDistance: '', dentalProsthesis: false, difficultAirwayRisk: '',
  medications: '', allergies: '', latexAllergy: false,
  hb: '', ht: '', platelets: '', inr: '', kptt: '', glucose: '', creatinine: '', urea: '', potassium: '', sodium: '', ecg: '', chestXray: '', echo: '', otherExams: '',
  emapoCriteria: [], ariscatAge: 0, ariscatSpo2: 0, ariscatInfection: 0, ariscatAnemia: 0, ariscatIncision: 0, ariscatDuration: 0, ariscatEmergency: 0,
  rcriCriteria: [], goldmanCriteria: [], apfelCriteria: [], conduct: 'Apto', anestheticTechnique: '', plan: '', monitoring: '', recommendations: ''
});
export const all = () => list('consultations');
export const find = (id) => get('consultations', id);
export const byAppointment = (appointmentId) => all().find((item) => item.appointmentId === appointmentId) || emptyConsultation(appointmentId);
export const saveForAppointment = (appointmentId, data) => upsertBy('consultations', (item) => item.appointmentId === appointmentId, { ...data, appointmentId });
export const finalize = (id, data) => update('consultations', id, { ...data, status: 'Finalizada', finishedAt: new Date().toISOString() });
