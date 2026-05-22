import axios from 'axios'
import { useUiStore } from '../stores/ui.js'

// When VITE_API_BASE is set (typically a full URL for prod), use it.
// Empty / unset → use relative '/api/...' so the page's origin (the Caddy proxy)
// resolves the API host. Avoids cross-origin / CORS preflight on dev :8081.
const baseURL = import.meta.env.VITE_API_BASE || ''

// axios v1 defaults to bracket-style array serialization (`key[]=a&key[]=b`)
// which FastAPI's `list[str] = Query(default=[])` doesn't recognise — it
// wants repeat keys (`key=a&key=b`). Without this, GET /api/patient/{id}/graph
// with scope=encounter silently returns 400 because the encounterId list
// arrives empty on the backend. `indexes: null` flips the global serializer
// to the repeat-key shape.
export const api = axios.create({
  baseURL,
  timeout: 120000,
  paramsSerializer: { indexes: null },
})

api.interceptors.request.use((config) => {
  const key = localStorage.getItem('cng_api_key')
  if (key) config.headers['X-API-Key'] = key
  return config
})

// Recognise an AbortController-cancelled request. axios raises this shape
// whenever a component aborts its in-flight requests on unmount / route
// change — which is normal app behaviour, not an error worth surfacing to
// the user. Without this guard a "canceled" snackbar pops every time you
// flip tabs mid-load.
const isCanceledError = (err) =>
  err?.code === 'ERR_CANCELED'
  || err?.name === 'CanceledError'
  || err?.name === 'AbortError'
  || axios.isCancel?.(err)
  || err?.message === 'canceled'

// Track whether we've already shown the API-key prompt this session so
// repeated 401s (e.g. every protected endpoint failing on first paint)
// don't spam the snackbar. Reset when the user actually sets a key
// (handled in ConfigView).
let apiKeyPromptShown = false

function isMissingApiKey401(err) {
  if (err.response?.status !== 401) return false
  const d = err.response.data?.detail
  return typeof d === 'string' && /X-API-Key/i.test(d)
}

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (isCanceledError(err)) {
      // Re-reject so callers' try/catch / .catch chains still see the
      // cancellation if they care, but skip the global snackbar.
      return Promise.reject(err)
    }
    if (err.config?.silent !== true) {
      try {
        const ui = useUiStore()
        if (isMissingApiKey401(err)) {
          // First 401 from a fresh deployment: the user has no idea
          // where to set the key. Pop a forced modal dialog (only
          // once, so repeated 401s on page-load don't re-trigger it)
          // instead of a transient toast — the snackbar was easy to
          // miss when every protected endpoint 401'd on first paint.
          if (!apiKeyPromptShown) {
            apiKeyPromptShown = true
            ui.promptApiKey()
          }
        } else {
          const msg = err.response?.data?.detail || err.message || 'Request failed'
          ui.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
        }
      } catch {
        // Pinia not ready yet at boot; ignore.
      }
    }
    return Promise.reject(err)
  },
)

// Reset the once-per-session 401 prompt so the user gets a fresh
// warning if they later clear / mistype the key.
export function resetApiKeyPromptShown() {
  apiKeyPromptShown = false
}

const data = (r) => r.data

export const listPatients = (q = '', signal) =>
  api.get('/api/patients', { params: q ? { q } : {}, signal }).then(data)
export const getPatient = (id, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}`, { signal }).then(data)
export const getTimeline = (id, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/timeline`, { signal }).then(data)
export const getGraph = (id, options = {}) => {
  const { signal, ...rest } = options
  // Filter out undefined values so they don't appear as `key=undefined` in the URL.
  const params = Object.fromEntries(
    Object.entries(rest).filter(([, v]) => v !== undefined && v !== null && v !== ''),
  )
  return api.get(`/api/patient/${encodeURIComponent(id)}/graph`, { params, signal }).then(data)
}
export const rebuildGraph = (id) =>
  api.post(`/api/patient/${encodeURIComponent(id)}/graph/rebuild`).then(data)
export const getNotes = (id, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/notes`, { signal }).then(data)
export const getNote = (id, path, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/note`, { params: { path }, signal }).then(data)
export const getEncounterDocuments = (id, encounterId, signal) =>
  api
    .get(`/api/patient/${encodeURIComponent(id)}/encounter/${encodeURIComponent(encounterId)}/documents`, { signal })
    .then(data)
// Single round-trip that powers the encounter dialog's Detail tab —
// returns {encounter, thisEncounter, background, documents}. Before this
// existed the dialog only fetched the encounter-level summary and rendered
// a fully-empty pane when the user hadn't generated one yet.
export const getEncounterFacts = (id, encounterId, signal) =>
  api
    .get(`/api/patient/${encodeURIComponent(id)}/encounter/${encodeURIComponent(encounterId)}/facts`, { signal })
    .then(data)
export const getDocument = (id, docId, signal) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/document/${encodeURIComponent(docId)}`, { signal }).then(data)
export const ingest = (body) => api.post('/api/emr/ingest', body).then(data)
export const getConfig = () => api.get('/api/config').then(data)
export const patchConfig = (patch) => api.patch('/api/config', patch).then(data)
export const listExportProfiles = () => api.get('/api/config/export-profiles').then(data)
export const upsertExportProfile = (p) =>
  api.put(`/api/config/export-profiles/${encodeURIComponent(p.profileId)}`, p).then(data)
// AI generation calls (summary, coding, RAG) bypass the default 120 s
// timeout because reasoning models (DeepSeek R1, GPT-5-mini in reasoning
// mode) routinely take 3-5 minutes on patients with many facts. A short
// timeout means axios abandons the request while the backend continues
// and persists the result — the user sees a perpetually-loading button
// and can't tell the result was actually saved. 6 minutes is the cap we
// give the user before bailing out for real.
const AI_GEN_TIMEOUT_MS = 360_000

export const summarize = (id, body) =>
  api.post(`/api/patient/${encodeURIComponent(id)}/summary`, body, { timeout: AI_GEN_TIMEOUT_MS }).then(data)
export const getLatestSummary = (id) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/summary/latest`).then(data)
export const suggestCoding = (id, body) =>
  api.post(`/api/patient/${encodeURIComponent(id)}/coding/suggest`, body, { timeout: AI_GEN_TIMEOUT_MS }).then(data)
export const getLatestCoding = (id) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/coding/latest`).then(data)
export const listEncounters = (id) =>
  api.get(`/api/patient/${encodeURIComponent(id)}/encounters`).then(data)
export const summarizeEncounter = (pid, eid, body) =>
  api.post(`/api/patient/${encodeURIComponent(pid)}/encounter/${encodeURIComponent(eid)}/summary`, body, { timeout: AI_GEN_TIMEOUT_MS }).then(data)
export const getLatestEncounterSummary = (pid, eid) =>
  api.get(`/api/patient/${encodeURIComponent(pid)}/encounter/${encodeURIComponent(eid)}/summary/latest`).then(data)
export const suggestEncounterCoding = (pid, eid, body) =>
  api.post(`/api/patient/${encodeURIComponent(pid)}/encounter/${encodeURIComponent(eid)}/coding/suggest`, body, { timeout: AI_GEN_TIMEOUT_MS }).then(data)
export const getLatestEncounterCoding = (pid, eid) =>
  api.get(`/api/patient/${encodeURIComponent(pid)}/encounter/${encodeURIComponent(eid)}/coding/latest`).then(data)
export const ragAsk = (body) =>
  api.post('/api/rag/ask', body, { timeout: AI_GEN_TIMEOUT_MS }).then(data)

// Queue-mode counterparts (PR for issue #25). Same endpoints with
// `?async=true` — return the queued-job envelope
// `{jobId, status: "queued", type, patientId[, encounterId]}`
// immediately so the UI can render the confirmation dialog without
// blocking on the 3-5 minute reasoning call. Scripted callers stick
// with the inline helpers above.
export const summarizeQueued = (id, body) =>
  api.post(`/api/patient/${encodeURIComponent(id)}/summary?async=true`, body).then(data)
export const suggestCodingQueued = (id, body) =>
  api.post(`/api/patient/${encodeURIComponent(id)}/coding/suggest?async=true`, body).then(data)
export const summarizeEncounterQueued = (pid, eid, body) =>
  api.post(`/api/patient/${encodeURIComponent(pid)}/encounter/${encodeURIComponent(eid)}/summary?async=true`, body).then(data)
export const suggestEncounterCodingQueued = (pid, eid, body) =>
  api.post(`/api/patient/${encodeURIComponent(pid)}/encounter/${encodeURIComponent(eid)}/coding/suggest?async=true`, body).then(data)
export const searchPatientsByVector = (q, limit = 10, signal) =>
  api.get('/api/search/patients', { params: { q, limit }, signal }).then(data)
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
