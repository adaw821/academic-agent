<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { ApiError, request, post, type Document } from '../api'
const props = defineProps<{ projectId: number; document: Document; locked: boolean }>()
const emit = defineEmits<{ error: [unknown]; changed: []; notice: [string] }>()
const busy = ref(false),
  content = ref<string | null>(null),
  confirmDelete = ref(false),
  confirmRestart = ref(false)
let alive = true
onUnmounted(() => {
  alive = false
})
async function action(work: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  try {
    await work()
  } catch (e) {
    if (alive) emit('error', e)
  } finally {
    busy.value = false
  }
}
function path() {
  return `/projects/${props.projectId}/documents/${props.document.id}`
}
function read() {
  void action(async () => {
    const result = await request<{ content: string }>(path() + '/content')
    if (alive)
      content.value = DOMPurify.sanitize(marked.parse(result.content, { async: false }) as string)
  })
}
function download() {
  void action(async () => {
    const response = await fetch('/api' + path() + '/file', { credentials: 'same-origin' })
    if (!response.ok) {
      const data = await response.json().catch(() => null)
      throw new ApiError(
        response.status,
        typeof data?.detail === 'string' ? data.detail : '原文件下载失败',
      )
    }
    const blob = await response.blob()
    if (!alive) return
    const url = URL.createObjectURL(blob),
      link = document.createElement('a')
    link.href = url
    link.download = props.document.filename
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  })
}
function retry(restart = false) {
  void action(async () => {
    await post(path() + `/retry${restart ? '?restart=true' : ''}`, {})
    if (alive) {
      confirmRestart.value = false
      content.value = null
      emit('notice', '文档已加入处理队列。')
      emit('changed')
    }
  })
}
function remove() {
  void action(async () => {
    const result = await request<{ cleanup_pending?: boolean }>(path(), { method: 'DELETE' })
    if (alive) {
      emit(
        'notice',
        result?.cleanup_pending ? '文档已删除，残留文件将在后台清理。' : '文档已删除。',
      )
      emit('changed')
    }
  })
}
</script>
<template>
  <div class="document-actions">
    <div class="field-row">
      <button class="text-button" :disabled="busy || document.status !== 'completed'" @click="read">
        查看正文</button
      ><button class="text-button" :disabled="busy" @click="download">下载原文件</button>
      <button
        v-if="document.status === 'failed'"
        class="text-button"
        :disabled="busy || locked"
        @click="retry()"
      >
        重试解析
      </button>
      <button
        v-if="document.status === 'completed'"
        class="text-button"
        :disabled="busy || locked"
        @click="
          () => {
            confirmRestart = !confirmRestart
            confirmDelete = false
          }
        "
      >
        重新处理
      </button>
      <button
        class="text-button danger-text"
        :disabled="busy || locked || ['queued', 'processing'].includes(document.status)"
        @click="
          () => {
            confirmDelete = !confirmDelete
            confirmRestart = false
          }
        "
      >
        删除文档
      </button>
    </div>
    <div v-if="confirmDelete" class="inline-confirm">
      <p>删除“{{ document.filename }}”及其检索内容？历史回答会保留。</p>
      <button class="subtle-button" :disabled="busy" @click="remove">确认删除文档</button
      ><button class="text-button" :disabled="busy" @click="confirmDelete = false">取消</button>
    </div>
    <div v-if="confirmRestart" class="inline-confirm">
      <p>重新处理期间，该文档暂不参与检索。</p>
      <button class="subtle-button" :disabled="busy" @click="retry(true)">确认重新处理</button
      ><button class="text-button" :disabled="busy" @click="confirmRestart = false">取消</button>
    </div>
    <section v-if="content !== null" class="document-content">
      <button class="text-button" @click="content = null">收起正文</button>
      <div class="markdown" v-html="content"></div>
    </section>
  </div>
</template>
