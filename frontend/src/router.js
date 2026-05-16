import { createRouter, createWebHashHistory } from 'vue-router'

import PatientsView from './views/PatientsView.vue'
import PatientDetail from './views/PatientDetail.vue'
import IngestView from './views/IngestView.vue'
import ConfigView from './views/ConfigView.vue'

const routes = [
  { path: '/', redirect: '/patients' },
  { path: '/patients', component: PatientsView, name: 'patients' },
  { path: '/patients/:id', component: PatientDetail, name: 'patient', props: true },
  { path: '/ingest', component: IngestView, name: 'ingest' },
  { path: '/config', component: ConfigView, name: 'config' },
]

export default createRouter({ history: createWebHashHistory(), routes })
