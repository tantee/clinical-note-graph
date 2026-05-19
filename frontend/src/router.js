import { createRouter, createWebHashHistory } from 'vue-router'

import PatientsView from './views/PatientsView.vue'
import PatientDetail from './views/PatientDetail.vue'
import IngestView from './views/IngestView.vue'
import ConfigView from './views/ConfigView.vue'
import DebugView from './views/DebugView.vue'

const routes = [
  { path: '/', redirect: '/patients' },
  { path: '/patients', component: PatientsView, name: 'patients' },
  { path: '/patients/:id', component: PatientDetail, name: 'patient', props: true },
  {
    path: '/patient/:id/encounter/:eid',
    name: 'encounter',
    component: () => import('./views/EncounterDetail.vue'),
    props: true,
  },
  {
    path: '/vector-demo',
    component: () => import('./views/VectorDemoView.vue'),
    name: 'vector-demo',
  },
  { path: '/ingest', component: IngestView, name: 'ingest' },
  { path: '/config', component: ConfigView, name: 'config' },
  { path: '/debug', component: DebugView, name: 'debug' },
]

export default createRouter({ history: createWebHashHistory(), routes })
