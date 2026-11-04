<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { request, localTime } from '../api'
const emit = defineEmits<{ supplement: [File] }>()
defineProps<{ recognizing?: boolean }>()
const preparing = ref(false)
async function useSnapshot() {
  if (preparing.value) return
  preparing.value = true
  error.value = ''
  try {
    const response = await fetch('/api/timetable/mis/image', { credentials: 'same-origin', cache: 'no-store' })
    if (!response.ok) throw new Error('读取学校课表图片失败，请刷新后重试。')
    const blob = await response.blob()
    if (alive) emit('supplement', new File([blob], 'MIS课表.png', { type: 'image/png' }))
  } catch (e) { if (alive) error.value = e instanceof Error ? e.message : '读取图片失败' }
  finally { preparing.value = false }
}
interface Snapshot {
  term: string
  saved_at: string
  courses: { course_code: string; course_name: string; teachers: string; times: string[]; rooms: string[]; units: string; remark: string }[]
}
interface Status { state: string; message: string }
const snapshot = ref<Snapshot | null>(null)
const enabled = ref(false)
const status = ref<Status>({ state: 'idle', message: '' })
const busy = ref(false)
const error = ref('')
let alive = true
let timer: ReturnType<typeof setTimeout> | undefined
const active = () => ['opening', 'waiting', 'ready', 'saving'].includes(status.value.state)
async function load() {
  const data = await request<{ local_login: boolean; snapshot: Snapshot | null }>('/timetable/mis')
  if (alive) { enabled.value = data.local_login; snapshot.value = data.snapshot }
}
async function update(value: Status) {
  if (!alive) return
  status.value = value
  clearTimeout(timer)
  if (value.state === 'saved') await load()
  if (alive && active()) timer = setTimeout(() => void poll(), 1500)
}
async function poll() {
  try { await update(await request<Status>('/timetable/mis/capture')) }
  catch (e) {
    if (alive) { error.value = e instanceof Error ? e.message : '无法读取进度'; status.value = { state: 'idle', message: '' } }
  }
}
async function act(method: string) {
  if (busy.value) return
  busy.value = true; error.value = ''; clearTimeout(timer)
  try { await update(await request<Status>('/timetable/mis/capture', { method })) }
  catch (e) {
    if (alive) error.value = e instanceof Error ? e.message : '操作失败'
    if (alive && active()) timer = setTimeout(() => void poll(), 1500)
  }
  finally { busy.value = false }
}
onMounted(async () => {
  try { await load(); if (alive) await poll() }
  catch (e) { if (alive) error.value = e instanceof Error ? e.message : '读取已保存课表失败' }
})
onUnmounted(() => { alive = false; clearTimeout(timer) })
</script>
<template>
  <section class="feature-card">
    <div class="field-row">
      <button v-if="enabled" class="primary-button" :disabled="busy || active()" @click="act('POST')">{{ snapshot ? '从 MIS 更新课表' : '从 MIS 保存课表' }}</button>
      <button v-if="status.state === 'ready'" class="primary-button" :disabled="busy" @click="act('PUT')">保存当前学期</button>
      <button v-if="active()" class="subtle-button" :disabled="busy" @click="act('DELETE')">取消</button>
      <a class="subtle-button" href="https://mis.bnbu.edu.cn/mis/student/tts/timetable.do" target="_blank" rel="noopener noreferrer">打开学校课表</a>
    </div>
    <p v-if="status.message" role="status">{{ status.message }}</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <template v-if="snapshot">
      <p>{{ snapshot.term }} · 保存于 {{ localTime(snapshot.saved_at) }} · {{ snapshot.courses.length }} 门课程</p>
      <button class="subtle-button" :disabled="preparing || recognizing || active()" @click="useSnapshot">用此图片补充课表</button>
      <details open>
        <summary>已保存的课表图片</summary>
        <img :src="`/api/timetable/mis/image?v=${encodeURIComponent(snapshot.saved_at)}`" alt="已保存的 MIS 课表" style="width:100%;height:auto" />
      </details>
      <details>
        <summary>课程明细</summary>
        <div v-for="course in snapshot.courses" :key="`${course.course_code}-${course.course_name}`" class="feature-card">
          <strong>{{ course.course_name }} · {{ course.course_code }}</strong>
          <p>{{ course.teachers }} · {{ course.units }} 学分</p>
          <p v-for="(time, index) in course.times" :key="index">{{ time }} · {{ course.rooms[index] || '地点未提供' }}</p>
          <p v-if="!course.times.length">学校暂未提供上课时间</p>
          <p v-if="course.remark">{{ course.remark }}</p>
        </div>
      </details>
    </template>
    <p v-else>尚未保存学校课表。也可以继续使用下方的图片上传识别。</p>
  </section>
</template>
