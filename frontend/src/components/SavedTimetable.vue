<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { request, type Course, type PageResult } from '../api'
const emit = defineEmits<{ error: [unknown] }>()
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
const editing = ref<Schedule | null>(null)
const form = ref({
  term_name: '',
  term_start: '',
  term_end: '',
  start_week: '',
  end_week: '',
  week_pattern: 'all',
})
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
function edit(row: Schedule) {
  editing.value = row
  form.value = {
    term_name: row.term_name || '',
    term_start: row.term_start || '',
    term_end: row.term_end || '',
    start_week: row.start_week?.toString() || '',
    end_week: row.end_week?.toString() || '',
    week_pattern: row.week_pattern || 'all',
  }
}
async function save(clear = false) {
  if (!editing.value || saving.value) return
  saving.value = true
  message.value = ''
  try {
    await request(`/timetable/${editing.value.id}/calendar`, {
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
    editing.value = null
    message.value = clear ? '已恢复为每周安排。' : '学期规则已保存。'
    await load()
  } catch (e) {
    if (alive) emit('error', e)
  } finally {
    saving.value = false
  }
}
</script>
<template>
  <section class="saved-timetable feature-card" aria-label="已保存课表">
    <div class="field-row">
      <h2>已保存课表</h2>
      <button class="subtle-button" :disabled="loading || saving" @click="load">
        刷新已保存课表
      </button>
    </div>
    <form
      class="field-row"
      @submit.prevent="
        () => {
          offset = 0
          load()
        }
      "
    >
      <label>按日期查看<input v-model="onDate" type="date" /></label
      ><button class="subtle-button" :disabled="loading || saving">查询课表</button
      ><button
        type="button"
        class="text-button"
        :disabled="loading || saving"
        @click="
          () => {
            onDate = ''
            offset = 0
            load()
          }
        "
      >
        查看每周安排
      </button>
    </form>
    <p class="feature-warning">
      北京时间；学期开始日所在周为第 1
      周，每周一递增。未配置学期的记录仅按星期匹配，临时调课需自行核对。
    </p>
    <p v-if="message" role="status">{{ message }}</p>
    <p v-if="loading">正在读取课表…</p>
    <p v-else-if="!rows.length" class="empty-feature">当前范围没有课表记录。</p>
    <div v-else class="saved-grid">
      <article v-for="row in rows" :key="row.id" class="feature-card">
        <h3>{{ row.course_name }}</h3>
        <p>{{ row.day_of_week }} · {{ row.start_time }}–{{ row.end_time }}</p>
        <p>{{ row.location || '地点未提供' }} · {{ row.teachers || '教师未提供' }}</p>
        <p>
          {{ row.term_name || '未命名学期' }} ·
          {{ { all: '每周', odd: '单周', even: '双周' }[row.week_pattern || 'all'] }}
        </p>
        <p v-if="row.term_start">
          {{ row.term_start }} 至 {{ row.term_end }} · 第 {{ row.start_week || 1 }}–{{
            row.end_week || '末'
          }}
          周
        </p>
        <p v-else>尚未配置学期</p>
        <p v-if="row.teaching_week">查询日期为第 {{ row.teaching_week }} 教学周</p>
        <button class="text-button" :disabled="saving" @click="edit(row)">设置学期与单双周</button>
      </article>
    </div>
    <div v-if="onDate" class="pagination">
      <button
        :disabled="loading || !offset"
        @click="
          () => {
            offset -= 20
            load()
          }
        "
      >
        上一页课表</button
      ><span>共 {{ total }} 条</span
      ><button
        :disabled="loading || offset + 20 >= total"
        @click="
          () => {
            offset += 20
            load()
          }
        "
      >
        下一页课表
      </button>
    </div>
    <form v-if="editing" class="calendar-form feature-card" @submit.prevent="save()">
      <h3>设置：{{ editing.course_name }}</h3>
      <div class="field-row">
        <label>学期名称<input v-model="form.term_name" maxlength="100" /></label
        ><label>学期开始<input v-model="form.term_start" type="date" required /></label
        ><label
          >学期结束<input v-model="form.term_end" type="date" :min="form.term_start" required
        /></label>
      </div>
      <div class="field-row">
        <label>起始教学周<input v-model="form.start_week" type="number" min="1" max="60" /></label
        ><label
          >结束教学周<input
            v-model="form.end_week"
            type="number"
            :min="Number(form.start_week) || 1"
            max="60" /></label
        ><label
          >周次规则<select v-model="form.week_pattern">
            <option value="all">每周</option>
            <option value="odd">单周</option>
            <option value="even">双周</option>
          </select></label
        >
      </div>
      <div class="field-row">
        <button class="primary-button" :disabled="saving">保存学期规则</button
        ><button type="button" class="subtle-button" :disabled="saving" @click="save(true)">
          清除学期规则</button
        ><button type="button" class="text-button" :disabled="saving" @click="editing = null">
          取消
        </button>
      </div>
    </form>
  </section>
</template>
