import axios from 'axios'
import { useUiStore } from '../stores/ui.js'

const baseURL = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

export const api = axios.create({ baseURL, timeout: 120000 })

api.interceptors.request.use((config) => {
  const key = localStorage.getItem('cng_api_key')
  if (key) config.headers['X-API-Key'] = key
  return config
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.config?.silent !== true) {
      const msg = err.response?.data?.detail || err.message || 'Request failed'
      try {
        const ui = useUiStore()
        ui.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
      } catch {
        // Pinia not ready yet at boot; ignore.
      }
    }
    return Promise.reject(err)
  },
)

const data = (r) => r.data

export const listPatients = (q = '', signal) =>
  api.get('/api/patients', { params: q ? { q } : {}, signal }).then(data)
export const getPatient = (id, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}`, { signal }).then(data)
export const getTimeline = (id, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/timeline`, { signal }).then(data)
export const getGraph = (id, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/graph`, { signal }).then(data)
export const getNotes = (id, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/notes`, { signal }).then(data)
export const getNote = (id, path, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/note`, { params: { path }, signal }).then(data)
export const getEncounterDocuments = (id, encounterId, signal) =>
  api
    .get(`/api/patient/${encodeURIComponent(id)}/encounter/${encodeURIComponent(encounterId)}/documents`, { signal })
    .then(data)
export const getDocument = (id, docId, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/document/${encodeURIComponent(docId)}`, { signal }).then(data)
export const ingest = (body) => api.post('/api/emr/ingest', body).then(data)
export const getConfig = () => api.get('/api/config').then(data)
export const patchConfig = (patch) => api.patch('/api/config', patch).then(data)
export const listExportProfiles = () => api.get('/api/config/export-profiles').then(data)
export const upsertExportProfile = (p) =>
  api.put(`/api/config/export-profiles/${encodeURIComponent(p.profileId)}`, p).then(data)
export const summarize = (id, body) =>
  api.post(`/api/patient/${encodeURIComponent(id)}/summary`, body).then(data)
export const getLatestSummary = (id) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/summary/latest`).then(data)
export const suggestCoding = (id, body) =>
  api.post(`/api/patient/${encodeURIComponent(id)}/coding/suggest`, body).then(data)
export const getLatestCoding = (id) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/coding/latest`).then(data)
export const listEncounters = (id) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/encounters`).then(data)
export const summarizeEncounter = (pid, eid, body) =>
  api.post(`/api/patient/${encodeURIComponent(pid)}/encounter/${encodeURIComponent(eid)}/summary`, body).then(data)
export const getLatestEncounterSummary = (pid, eid) =>
  api.get(`/api/patient/${encodeURIComponent(pid)}/encounter/${encodeURIComponent(eid)}/summary/latest`).then(data)
export const suggestEncounterCoding = (pid, eid, body) =>
  api.post(`/api/patient/${encodeURIComponent(pid)}/encounter/${encodeURIComponent(eid)}/coding/suggest`, body).then(data)
export const getLatestEncounterCoding = (pid, eid) =>
  api.get(`/api/patient/${encodeURIComponent(pid)}/encounter/${encodeURIComponent(eid)}/coding/latest`).then(data)
export const reviewFact = (factId, status) =>
  api.patch(`/api/facts/${factId}/review`, null, { params: { status } }).then(data)
export const exportPatient = (body) => api.post('/api/export', body).then(data)

export const getJob = (jobId, signal) =>
  api.get(`/api/jobs/${encodeURIComponent(jobId)}`, { signal }).then(data)
export const listJobs = (params, signal) =>
  api.get('/api/jobs', { params, signal }).then(data)
export const requeueJob = (jobId) =>
  api.post(`/api/jobs/${encodeURIComponent(jobId)}/requeue`).then(data)

export const getDebugSummary = (params, signal) =>
  api.get('/api/debug/summary', { params, signal }).then(data)
export const getDebugByModel = (params, signal) =>
  api.get('/api/debug/by-model', { params, signal }).then(data)
export const getDebugByDay = (params, signal) =>
  api.get('/api/debug/by-day', { params, signal }).then(data)
export const listAiCalls = (params, signal) =>
  api.get('/api/debug/ai-calls', { params, signal }).then(data)
export const getAiCall = (id, signal) =>
  api.get(`/api/debug/ai-calls/${encodeURIComponent(id)}`, { signal }).then(data)

export const listPricing = () => api.get('/api/config/pricing').then(data)
export const upsertPricing = (model, body) =>
  api.put(`/api/config/pricing/${encodeURIComponent(model)}`, body).then(data)
export const deletePricing = (model) =>
  api.delete(`/api/config/pricing/${encodeURIComponent(model)}`).then(data)
export const refreshOpenRouter = () =>
  api.post('/api/config/pricing/refresh-openrouter').then(data)
