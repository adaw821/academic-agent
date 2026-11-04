<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { request, type Course, type PageResult } from '../api'
const emit = defineEmits<{ error: [unknown]; changed: [] }>()
interface Schedule extends Course {
  id: number
  course_id: number
  term_name: string | null
  term_start: string | null
  term_end: string | null
  start_week: number | null
  end_week: number | null
  week_pattern: 'all' | 'odd' | 'even'
  teaching_week?: number | null
}
const rows = ref<Schedule[]>([]),
  total = ref(0),
  offset = ref(0),
  onDate = ref(''),
  loading = ref(false),
  saving = ref(false),
  message = ref('')
const weekdays = [['Mon', '周一'], ['Tue', '周二'], ['Wed', '周三'], ['Thu', '周四'], ['Fri', '周五'], ['Sat', '周六'], ['Sun', '周日']]
 
/* 展开行：null 表示全部收起；mode 控制该行下方显示什么面板 */
const expandedRow = ref<Schedule | null>(null)
type ExpandMode = 'term' | 'time' | 'delete' | null
const expandedMode = ref<ExpandMode>(null)
function toggleExpand(row: Schedule, mode: ExpandMode) {
  if (expandedRow.value?.id === row.id && expandedMode.value === mode) {
    expandedRow.value = null
    expandedMode.value = null
  } else {
    expandedRow.value = row
    expandedMode.value = mode
  }
  message.value = ''
}
function closeExpand() {
  expandedRow.value = null
  expandedMode.value = null
}
 
/* 手动新增（不在某行展开，单独顶部面板） */
const entryOpen = ref(false)
const entryId = ref<number | null>(null)
const entry = ref({ course_name: '', course_code: '', teachers: '', day_of_week: 'Mon', start_time: '09:00', end_time: '10:00', location: '' })
function openEntry(row?: Schedule) {
  expandedRow.value = null
  expandedMode.value = null
  entryId.value = row?.id ?? null
  entry.value = {
    course_name: row?.course_name || '',
    course_code: row?.course_code || '',
    teachers: row?.teachers || '',
    day_of_week: String(row?.day_of_week || 'Mon'),
    start_time: row?.start_time || '09:00',
    end_time: row?.end_time || '10:00',
    location: row?.location || '',
  }
  entryOpen.value = true
  message.value = ''
}
 
async function saveEntry() {
  if (saving.value) return
  if (entry.value.end_time <= entry.value.start_time) { message.value = '结束时间必须晚于开始时间。'; return }
  saving.value = true
  try {
    const { day_of_week, start_time, end_time, location } = entry.value
    await request(entryId.value === null ? '/timetable/entries' : `/timetable/entries/${entryId.value}`, {
      method: entryId.value === null ? 'POST' : 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entryId.value === null ? entry.value : { day_of_week, start_time, end_time, location }),
    })
    if (!alive) return
    entryOpen.value = false
    closeExpand()
    onDate.value = ''; offset.value = 0
    message.value = entryId.value === null ? '已添加上课安排。' : '上课安排已修改。'
    emit('changed')
    await load()
  } catch (e) { if (alive) emit('error', e) }
  finally { saving.value = false }
}
 
async function deleteEntry() {
  if (!expandedRow.value || expandedMode.value !== 'delete' || saving.value) return
  const target = expandedRow.value
  saving.value = true
  try {
    await request(`/timetable/entries/${target.id}`, { method: 'DELETE' })
    if (!alive) return
    closeExpand()
    offset.value = 0
    message.value = '已删除这条上课安排。'
    emit('changed')
    await load()
  } catch (e) { if (alive) emit('error', e) }
  finally { saving.value = false }
}
 
/* 学期设置表单（按行展开） */
const form = ref({
  term_name: '',
  term_start: '',
  term_end: '',
  start_week: '',
  end_week: '',
  week_pattern: 'all',
})
function openTermForm(row: Schedule) {
  expandedRow.value = row
  expandedMode.value = 'term'
  form.value = {
    term_name: row.term_name || '',
    term_start: row.term_start || '',
    term_end: row.term_end || '',
    start_week: row.start_week?.toString() || '',
    end_week: row.end_week?.toString() || '',
    week_pattern: row.week_pattern || 'all',
  }
  message.value = ''
}
 
async function saveTerm(clear = false) {
  if (!expandedRow.value || saving.value) return
  const row = expandedRow.value
  saving.value = true
  message.value = ''
  try {
    await request(`/timetable/${row.id}/calendar`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(
        clear
          ? {}
          : {
              term_name: form.value.term_name || null,
              term_start: form.value.term_start || null,
              term_end: form.value.term_end || null,
              start_week: form.value.start_week ? Number(form.value.start_week) : null,
              end_week: form.value.end_week ? Number(form.value.end_week) : null,
              week_pattern: form.value.week_pattern,
            },
      ),
    })
    if (!alive) return
    closeExpand()
    message.value = clear ? '已恢复为每周安排。' : '学期规则已保存。'
    await load()
  } catch (e) {
    if (alive) emit('error', e)
  } finally {
    saving.value = false
  }
}
 
let generation = 0,
  alive = true
onUnmounted(() => {
  alive = false
  generation++
})
async function load() {
  const token = ++generation
  loading.value = true
  try {
    const result = await request<PageResult<Schedule>>(
      `/timetable${onDate.value ? `?on_date=${onDate.value}&limit=20&offset=${offset.value}` : ''}`,
    )
    if (alive && token === generation) {
      rows.value = result.items
      total.value = result.total
    }
  } catch (e) {
    if (alive && token === generation) emit('error', e)
  } finally {
    if (token === generation) loading.value = false
  }
}
defineExpose({ reload: load })
onMounted(load)
</script>
 
<template>
  <section class="saved-timetable feature-card" aria-label="已保存课表">
    <div class="field-row">
      <h2>已保存的课程信息</h2>
      <button class="primary-button" :disabled="loading || saving" @click="openEntry()">手动新增安排</button>
      <button class="subtle-button" :disabled="loading || saving" @click="load">刷新</button>
    </div>
    <p>这里的安排用于课表查询。同一门课每个上课时段单独一行。</p>
 
    <!-- 手动新增面板（顶部，不属于某行） -->
    <form v-if="entryOpen" class="inline-form feature-card" @submit.prevent="saveEntry">
      <h3>{{ entryId === null ? '新增上课安排' : '修改上课安排' }}</h3>
      <div class="field-row">
        <label>课程名称<input v-model="entry.course_name" required maxlength="500" :disabled="saving || entryId !== null" /></label>
        <label>课程编号<input v-model="entry.course_code" maxlength="20" :disabled="saving || entryId !== null" /></label>
        <label>教师<input v-model="entry.teachers" maxlength="100" :disabled="saving || entryId !== null" /></label>
      </div>
      <div class="field-row">
        <label>上课星期<select v-model="entry.day_of_week" :disabled="saving"><option v-for="day in weekdays" :key="day[0]" :value="day[0]">{{ day[1] }}</option></select></label>
        <label>开始时间<input v-model="entry.start_time" type="time" required :disabled="saving" /></label>
        <label>结束时间<input v-model="entry.end_time" type="time" required :disabled="saving" /></label>
        <label>上课地点<input v-model="entry.location" maxlength="500" :disabled="saving" /></label>
      </div>
      <button class="primary-button" :disabled="saving">保存</button>
      <button type="button" class="text-button" :disabled="saving" @click="entryOpen = false">取消</button>
    </form>
 
    <!-- 查询/筛选 -->
    <form
      class="field-row query-row"
      @submit.prevent="offset = 0; load()"
    >
      <label>按日期查看<input v-model="onDate" type="date" /></label>
      <button class="subtle-button" :disabled="loading || saving">查询</button>
      <button type="button" class="text-button" :disabled="loading || saving"
        @click="onDate = ''; offset = 0; load()">查看每周安排</button>
    </form>
 
    <p v-if="message" role="status" class="form-hint">{{ message }}</p>
    <p v-if="loading">正在读取课表…</p>
    <p v-else-if="!rows.length" class="empty-feature">当前范围没有课表记录。</p>
 
    <!-- 行式列表 -->
    <div v-else class="saved-list">
      <template v-for="row in rows" :key="row.id">
        <article class="saved-row" :class="{ expanded: expandedRow?.id === row.id }">
          <div class="saved-row-main">
            <div class="saved-row-info">
              <div class="saved-row-title">
                <strong>{{ row.course_name }}</strong>
                <span v-if="row.course_code" class="row-code">{{ row.course_code }}</span>
              </div>
              <div class="saved-row-meta">
                <span class="meta-chip">{{ { Mon:'周一', Tue:'周二', Wed:'周三', Thu:'周四', Fri:'周五', Sat:'周六', Sun:'周日' }[row.day_of_week] }} · {{ row.start_time }}–{{ row.end_time }}</span>
                <span class="meta-chip">{{ row.location || '地点未提供' }}</span>
                <span class="meta-chip">{{ row.teachers || '教师未提供' }}</span>
                <span class="meta-chip">{{ row.term_name || '未命名学期' }} · {{ { all:'每周', odd:'单周', even:'双周' }[row.week_pattern || 'all'] }}</span>
                <span v-if="row.term_start" class="meta-chip subtle">
                  {{ row.term_start }} 至 {{ row.term_end }} · 第 {{ row.start_week || 1 }}–{{ row.end_week || '末' }} 周
                </span>
              </div>
            </div>
            <div class="saved-row-actions">
              <button class="row-btn" :disabled="saving" @click="openTermForm(row)">设置学期</button>
              <button class="row-btn" :disabled="saving" @click="toggleExpand(row, 'term')">单双周</button>
              <button class="row-btn" :disabled="saving" @click="openEntry(row)">修改时间地点</button>
              <button class="row-btn row-btn-danger" :disabled="saving" @click="toggleExpand(row, 'delete')">删除</button>
            </div>
          </div>
 
          <!-- 该行下方展开：学期设置 -->
          <div v-if="expandedRow?.id === row.id && expandedMode === 'term'" class="row-expand">
            <form class="inline-form" @submit.prevent="saveTerm()">
              <h4>设置学期与单双周</h4>
              <div class="field-row">
                <label>学期名称<input v-model="form.term_name" maxlength="100" /></label>
                <label>学期开始<input v-model="form.term_start" type="date" required /></label>
                <label>学期结束<input v-model="form.term_end" type="date" :min="form.term_start" required /></label>
              </div>
              <div class="field-row">
                <label>起始教学周<input v-model="form.start_week" type="number" min="1" max="60" /></label>
                <label>结束教学周<input v-model="form.end_week" type="number" :min="Number(form.start_week) || 1" max="60" /></label>
                <label>周次规则<select v-model="form.week_pattern">
                  <option value="all">每周</option>
                  <option value="odd">单周</option>
                  <option value="even">双周</option>
                </select></label>
              </div>
              <button class="primary-button" :disabled="saving">保存学期规则</button>
              <button type="button" class="subtle-button" :disabled="saving" @click="saveTerm(true)">清除</button>
              <button type="button" class="text-button" :disabled="saving" @click="closeExpand">收起</button>
            </form>
          </div>
 
          <!-- 该行下方展开：删除确认 -->
          <div v-if="expandedRow?.id === row.id && expandedMode === 'delete'" class="row-expand row-expand-danger">
            <p>删除「{{ row.course_name }}」{{ { Mon:'周一', Tue:'周二', Wed:'周三', Thu:'周四', Fri:'周五', Sat:'周六', Sun:'周日' }[row.day_of_week] }} {{ row.start_time }}–{{ row.end_time }} 的安排？课程资料和其他时段会保留。</p>
            <button class="row-btn row-btn-danger" :disabled="saving" @click="deleteEntry">确认删除</button>
            <button type="button" class="text-button" :disabled="saving" @click="closeExpand">取消</button>
          </div>
        </article>
      </template>
    </div>
 
    <div v-if="onDate && rows.length" class="pagination">
      <button :disabled="loading || !offset" @click="offset -= 20; load()">上一页</button>
      <span>共 {{ total }} 条</span>
      <button :disabled="loading || offset + 20 >= total" @click="offset += 20; load()">下一页</button>
    </div>
  </section>
</template>