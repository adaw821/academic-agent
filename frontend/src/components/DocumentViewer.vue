<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { renderAsync } from 'docx-preview'
import { ZoomIn, ZoomOut, Hand } from 'lucide-vue-next'
import { ApiError, type Document } from '../api'
 
pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl
 
const props = defineProps<{ projectId: number; document: Document }>()
const emit = defineEmits<{ error: [unknown]; close: [] }>()
 
const busy = ref(false)
const host = ref<HTMLElement | null>(null)
const scale = ref(1.5)
const MIN_SCALE = 0.3, MAX_SCALE = 4, SCALE_STEP = 0.15
const panMode = ref(false) // 手掌拖动模式
 
// 拖动状态
let dragging = false
let lastX = 0, lastY = 0
 
let alive = true
let loading: pdfjsLib.PDFDocumentLoadingTask | null = null
 
// 保存每个 PDF 页面的原始像素尺寸（scale=1 时的 viewport）
const baseSizes: Array<{ width: number; height: number }> = []
 
const ext = computed(() => props.document.filename.split('.').pop()?.toLowerCase() ?? '')
 
function release() {
  void loading?.destroy()
  loading = null
}
 
onUnmounted(() => {
  alive = false
  release()
})
 
async function fetchFile() {
  const response = await fetch(`/api/projects/${props.projectId}/documents/${props.document.id}/file`, {
    credentials: 'same-origin',
  })
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new ApiError(
      response.status,
      typeof data?.detail === 'string' ? data.detail : '原文件读取失败',
    )
  }
  return response.arrayBuffer()
}
 
// 把 scale 应用到所有已渲染的 viewer-page（改 CSS width/height，不是 transform）
function applyScaleToPages() {
  if (!host.value) return
  const pages = host.value.querySelectorAll('.viewer-page') as NodeListOf<HTMLElement>
  pages.forEach((page, i) => {
    const base = baseSizes[i]
    if (!base) return
    const w = Math.round(base.width * scale.value)
    const h = Math.round(base.height * scale.value)
    page.style.width = w + 'px'
    page.style.height = h + 'px'
    const canvas = page.querySelector('canvas') as HTMLCanvasElement | null
    if (canvas) {
      canvas.style.width = w + 'px'
      canvas.style.height = h + 'px'
    }
    const text = page.querySelector('.textLayer') as HTMLElement | null
    if (text) {
      text.style.width = w + 'px'
      text.style.height = h + 'px'
      text.style.setProperty('--total-scale-factor', String(scale.value))
    }
  })
}
 
// 监听 scale 变化，重新应用到页面
watch(scale, () => {
  if (ext.value === 'pdf') applyScaleToPages()
})
 
async function renderPdf(data: ArrayBuffer, target: HTMLElement) {
  loading = pdfjsLib.getDocument({ data })
  const doc = await loading.promise
  baseSizes.length = 0
 
  for (let number = 1; number <= doc.numPages; number++) {
    const page = await doc.getPage(number)
    // 始终以 scale=1 渲染 canvas（高分辨率），之后用 CSS 改显示尺寸
    const vp = page.getViewport({ scale: 1 })
    baseSizes.push({ width: vp.width, height: vp.height })
 
    const box = document.createElement('div')
    box.className = 'viewer-page'
 
    const canvas = document.createElement('canvas')
    const ratio = window.devicePixelRatio || 1
    canvas.width = Math.floor(vp.width * ratio)
    canvas.height = Math.floor(vp.height * ratio)
    box.appendChild(canvas)
 
    const text = document.createElement('div')
    text.className = 'textLayer'
    box.appendChild(text)
 
    target.appendChild(box)
    if (!alive) return
 
    await page.render({
      canvas,
      canvasContext: canvas.getContext('2d')!,
      viewport: vp,
      transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
    }).promise
    await new pdfjsLib.TextLayer({
      textContentSource: page.streamTextContent(),
      container: text,
      viewport: vp,
    }).render()
    page.cleanup()
  }
 
  // 初始自适应：按容器宽度算 scale
  nextTick(() => {
    if (!target.clientWidth || baseSizes.length === 0) return
    const fit = (target.clientWidth - 24) / baseSizes[0].width // 留 16px padding
    scale.value = Math.max(MIN_SCALE, Math.min(MAX_SCALE, Math.round(fit * 100) / 100))
    applyScaleToPages()
  })
}
 
async function renderDocx(data: ArrayBuffer, target: HTMLElement) {
  await renderAsync(data, target, target, {
    inWrapper: true,
    breakPages: true,
    ignoreLastRenderedPageBreak: true,
  })
  // DOCX 用 CSS zoom（浏览器原生支持，清晰）
  nextTick(() => {
    if (target.clientWidth) {
      scale.value = 1
    }
  })
}
 
// 监听容器宽度变化 → PDF 自动重新 fit
let ro: ResizeObserver | null = null
onMounted(() => {
  if (!host.value) return
  ro = new ResizeObserver(() => {
    if (!host.value || ext.value !== 'pdf' || baseSizes.length === 0) return
    // 只在接近 fit 状态时（用户没主动 zoom 过）自动调整
    const fit = (host.value.clientWidth - 24) / baseSizes[0].width
    const diff = Math.abs(fit - scale.value) / fit
    if (diff > 0.15) {
      // 差距大 → 重新 fit
      scale.value = Math.max(MIN_SCALE, Math.min(MAX_SCALE, Math.round(fit * 100) / 100))
      applyScaleToPages()
    }
  })
  window.addEventListener('mousemove', onHostMouseMove)
  window.addEventListener('mouseup', onHostMouseUp)
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => {
  ro?.disconnect()
  window.removeEventListener('mousemove', onHostMouseMove)
  window.removeEventListener('mouseup', onHostMouseUp)
  window.removeEventListener('keydown', onKey)
})
 
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && panMode.value) panMode.value = false
}
 
async function load() {
  busy.value = true
  baseSizes.length = 0
  try {
    const data = await fetchFile()
    if (!alive || !host.value) return
    host.value.innerHTML = ''
    if (ext.value === 'pdf') await renderPdf(data, host.value)
    else await renderDocx(data, host.value)
  } catch (e) {
    if (alive) emit('error', e)
  } finally {
    if (alive) busy.value = false
  }
}
 
watch(() => props.document.id, load, { immediate: true })
 
function zoomIn() {
  scale.value = Math.min(MAX_SCALE, Math.round((scale.value + SCALE_STEP) * 100) / 100)
}
function zoomOut() {
  scale.value = Math.max(MIN_SCALE, Math.round((scale.value - SCALE_STEP) * 100) / 100)
}
function zoomFit() {
  if (!host.value || baseSizes.length === 0) return
  const fit = (host.value.clientWidth - 24) / baseSizes[0].width
  scale.value = Math.max(MIN_SCALE, Math.min(MAX_SCALE, Math.round(fit * 100) / 100))
  applyScaleToPages()
}
 
// ========== 手掌拖动 ==========
function onHostMouseDown(e: MouseEvent) {
  if (!panMode.value || !host.value) return
  dragging = true
  lastX = e.clientX
  lastY = e.clientY
  host.value.classList.add('is-grabbing')
  e.preventDefault()
}
function onHostMouseMove(e: MouseEvent) {
  if (!dragging || !host.value) return
  const dx = e.clientX - lastX
  const dy = e.clientY - lastY
  lastX = e.clientX
  lastY = e.clientY
  host.value.scrollLeft -= dx
  host.value.scrollTop -= dy
}
function onHostMouseUp() {
  if (!dragging || !host.value) return
  dragging = false
  host.value.classList.remove('is-grabbing')
}
 
const pct = computed(() => `${Math.round(scale.value * 100)}%`)
</script>
 
<template>
  <section class="document-viewer" :class="['doc-viewer-' + ext]">
    <div class="viewer-toolbar">
      <div class="viewer-toolbar-left">
        <span class="viewer-docname" :title="document.filename">{{ document.filename }}</span>
      </div>
      <div class="viewer-toolbar-right">
        <button
          class="zoom-btn pan-toggle"
          :class="{ active: panMode }"
          :title="panMode ? '退出手掌模式（Esc）' : '手掌拖动模式'"
          @click="panMode = !panMode"
        >
          <Hand :size="14" />
        </button>
        <button class="zoom-btn" :disabled="scale <= MIN_SCALE" @click="zoomOut" aria-label="缩小">
          <ZoomOut :size="14" />
        </button>
        <button class="zoom-pct" @click="zoomFit" :title="'适应宽度（当前 ' + pct + '）'">{{ pct }}</button>
        <button class="zoom-btn" :disabled="scale >= MAX_SCALE" @click="zoomIn" aria-label="放大">
          <ZoomIn :size="14" />
        </button>
      </div>
    </div>
    <div v-if="busy" class="viewer-hint">正在加载原文件…</div>
    <div
      v-show="!busy"
      ref="host"
      class="viewer-host"
      :class="{ 'pan-mode': panMode }"
      :style="ext === 'docx' ? { zoom: String(scale) } : {}"
      @mousedown="onHostMouseDown"
    ></div>
  </section>
</template>