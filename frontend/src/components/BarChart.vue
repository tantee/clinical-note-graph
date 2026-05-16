<template><Bar :data="data" :options="options" /></template>
<script setup>
import { Bar } from 'vue-chartjs'
import { Chart, BarElement, CategoryScale, LinearScale, Tooltip, Legend, Title } from 'chart.js'
Chart.register(BarElement, CategoryScale, LinearScale, Tooltip, Legend, Title)
defineProps({
  data: { type: Object, required: true },
  options: { type: Object, default: () => ({
    responsive: true,
    plugins: { legend: { position: 'bottom' } },
    scales: { x: { stacked: true }, y: { stacked: true } },
  })},
})
</script>
