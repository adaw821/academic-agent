<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { request, post, statusLabel, localTime, type PageResult, type Job } from '../api'
const props = defineProps<{ projectId: number; locked: boolean }>()
const emit = defineEmits<{ error: [unknown]; conversation: [number]; refresh: []; deleted: [number] }>()
interface Conversation {
  id: number
  title: string | null
  updated_at: string
  message_count: number
}
const tab = ref<'history' | 'jobs'>('history'),
  offset = ref(0),
  busy = ref(false),
  error = ref('')
const history = ref<PageResult<Conversation>>({ items: [], total: 0 }),
  jobs = ref<PageResult<Job>>({ items: [], total: 0 })
/** 正在二次确认删除的会话 id */
const pendingDelete = ref<number | null>(null)
let alive = true,
  token = 0,
  timer: ReturnType<typeof setTimeout> | undefined
onUnmounted(() => {
  alive = false
  token++
  clearTimeout(timer)
})
async function load() {
  const run = ++token
  busy.value = true
  error.value = ''
  clearTimeout(timer)
  try {
    if (tab.value === 'history') {
      const result = await request<PageResult<Conversation>>(
        `/projects/${props.projectId}/conversations?limit=20&offset=${offset.value}`,
      )
      if (alive && token === run) history.value = result
    } else {
      const result = await request<PageResult<Job>>(
        `/projects/${props.projectId}/jobs?limit=20&offset=${offset.value}`,
      )
      if (alive && token === run) {
        jobs.value = result
        if (result.items.some((j) => ['running', 'queued'].includes(j.status)))
          timer = setTimeout(load, 4000)
      }
    }
  } catch (e) {
    if (alive && token === run) {
      error.value = '列表读取失败，请重试。'
      emit('error', e)
    }
  } finally {
    if (token === run) busy.value = false
  }
}
watch(tab, () => {
  offset.value = 0
  void load()
})
onMounted(load)
async function retry(job: Job) {
  busy.value = true
  clearTimeout(timer)
  try {
    await post(
      job.kind === 'qa'
        ? `/projects/${props.projectId}/jobs/${job.job_id}/retry`
        : `/projects/${props.projectId}/documents/${job.document_id}/retry`,
      {},
    )
    if (alive) {
      emit('refresh')
      await load()
    }
  } catch (e) {
    if (alive) emit('error', e)
  } finally {
    busy.value = false
  }
}
async function remove(item: Conversation) {
  busy.value = true
  error.value = ''
  try {
    await request(`/projects/${props.projectId}/conversations/${item.id}`, { method: 'DELETE' })
    if (!alive) return
    pendingDelete.value = null
    // 删掉的正好是当前页最后一条时回退一页，避免停在空页
    if (history.value.items.length === 1 && offset.value > 0) offset.value -= 20
    await load()
    if (alive) emit('deleted', item.id)
  } catch (e) {
    if (alive) {
      error.value = '删除会话失败，请重试。'
      emit('error', e)
    }
  } finally {
    busy.value = false
  }
}
async function openResult(job: Job) {
  busy.value = true
  try {
    const result = await request<Job>(`/projects/${props.projectId}/jobs/${job.job_id}`)
    if (alive && result.result?.conversation_id) emit('conversation', result.result.conversation_id)
  } catch (e) {
    if (alive) emit('error', e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <section class="project-activity feature-card" aria-label="项目历史与任务">
    <nav class="feature-tabs">
      <button :aria-pressed="tab === 'history'" @click="tab = 'history'">历史会话</button
      ><button :aria-pressed="tab === 'jobs'" @click="tab = 'jobs'">后台任务</button
      ><button :disabled="busy" @click="load">刷新记录</button>
    </nav>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="busy" role="status">正在读取或更新…</p>
    <template v-if="tab === 'history'"
      ><p v-if="!busy && !history.items.length">暂无已保存会话。</p>
      <div v-for="item in history.items" :key="item.id" class="history-item">
        <div class="history-item-head">
          <button
            class="history-row"
            :disabled="locked || busy"
            @click="emit('conversation', item.id)"
          >
            <strong>{{ item.title || '未命名会话' }}</strong
            ><span>{{ item.message_count }} 条消息 · {{ localTime(item.updated_at) }}</span>
          </button>
          <button
            class="history-delete"
            :disabled="locked || busy"
            :aria-label="`删除会话：${item.title || '未命名会话'}`"
            @click="pendingDelete = pendingDelete === item.id ? null : item.id"
          >
            删除
          </button>
        </div>
        <div v-if="pendingDelete === item.id" class="inline-confirm">
          <p>
            删除“{{ item.title || '未命名会话' }}”及其 {{ item.message_count }} 条消息？该会话写入文件的批注也会一并撤销。
          </p>
          <button class="subtle-button" :disabled="busy" @click="remove(item)">确认删除会话</button
          ><button class="text-button" :disabled="busy" @click="pendingDelete = null">取消</button>
        </div>
      </div></template
    >
    <template v-else
      ><p v-if="!busy && !jobs.items.length">暂无任务。</p>
      <article v-for="job in jobs.items" :key="job.job_id" class="resource-row">
        <strong
          >{{ job.kind === 'qa' ? '问答' : `文档 #${job.document_id}` }} ·
          {{ statusLabel(job.status) }}</strong
        ><small class="job-id">{{ job.job_id }}</small>
        <p v-if="job.error_type">失败原因类型：{{ job.error_type }}</p>
        <div class="field-row">
          <button
            v-if="job.status === 'failed'"
            class="subtle-button"
            :disabled="busy || locked"
            @click="retry(job)"
          >
            重试任务</button
          ><button
            v-if="job.status === 'completed' && job.kind === 'qa'"
            class="text-button"
            :disabled="busy || locked"
            @click="openResult(job)"
          >
            打开任务回答
          </button>
        </div>
      </article></template
    >
    <div class="pagination">
      <button
        :disabled="busy || !offset"
        @click="
          () => {
            offset -= 20
            load()
          }
        "
      >
        上一页记录</button
      ><span>共 {{ tab === 'history' ? history.total : jobs.total }} 条</span
      ><button
        :disabled="busy || offset + 20 >= (tab === 'history' ? history.total : jobs.total)"
        @click="
          () => {
            offset += 20
            load()
          }
        "
      >
        下一页记录
      </button>
    </div>
  </section>
</template>
