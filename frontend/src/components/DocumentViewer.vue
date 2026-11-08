<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { renderAsync } from 'docx-preview'
import { init as initPptxPreview } from 'pptx-preview'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { ArrowDownToLine, ZoomIn, ZoomOut, Hand, Highlighter, StickyNote } from 'lucide-vue-next'
import { ApiError, type CitationHighlight, type Document } from '../api'
import { applyCitationHighlights, clearCitationHighlights } from './citationHighlight'
 
pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl
 
const props = defineProps<{
  projectId: number
  document: Document
  highlights?: CitationHighlight[]
  focus?: number | null
  /** 正在往文件副本写批注 */
  annotating?: boolean
  /** 已经写入过批注的引用编号（每条引用独立标记） */
  annotatedIndices?: number[]
}>()
const emit = defineEmits<{
  error: [unknown]
  close: []
  /** 只给某一条引用写批注 */
  annotate: [index: number]
  downloadAnnotated: []
}>()

// 已写批注的引用编号集合
const annotatedSet = computed(() => new Set(props.annotatedIndices ?? []))
 
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
// PPT/PPTX 优先用后端转好的 PDF（高保真，后端会依次尝试 LibreOffice / PowerPoint）；
// 转不出 PDF 时降级为纯前端 pptx-preview（无需本机部件），最后兜底展示解析出的文字。
const renderExt = computed(() => (['ppt', 'pptx'].includes(ext.value) ? 'pdf' : ext.value))
// 实际生效的渲染模式（降级后与 renderExt 不同）
const mode = ref<'pdf' | 'docx' | 'pptx' | 'text'>('pdf')

// ========== 引用高亮 ==========
const highlightMode = ref(false) // 有高亮时才显示导航条与"关闭高亮"按钮
const highlightMissed = ref(false) // 传入了引用但全部没匹配上时给出提示
const navItems = ref<number[]>([]) // 已命中的引用编号
const activeCite = ref<number | null>(null)
let rendered = false

function clearHighlights() {
  if (host.value) clearCitationHighlights(host.value)
  navItems.value = []
  activeCite.value = null
  highlightMode.value = false
  highlightMissed.value = false
}

function gotoCitation(index: number) {
  const marks = host.value?.querySelectorAll<HTMLElement>(`mark.cite-hit[data-cite="${index}"]`)
  if (!marks?.length) return
  activeCite.value = index
  marks[0].scrollIntoView({ behavior: 'smooth', block: 'center' })
  marks.forEach((m) => m.classList.add('is-focus'))
  window.setTimeout(() => marks.forEach((m) => m.classList.remove('is-focus')), 2000)
}

// 当前选中的那条引用是否已经写过批注（写完按钮置灰）
const activeAnnotated = computed(
  () => activeCite.value != null && annotatedSet.value.has(activeCite.value),
)
const canWriteActive = computed(
  () => activeCite.value != null && !props.annotating && !activeAnnotated.value,
)
const writeTitle = computed(() => {
  if (props.annotating) return '正在写入批注…'
  if (activeCite.value == null) return '先点左侧引用编号，选中要写入的那条引用'
  if (activeAnnotated.value) return `引用 [${activeCite.value}] 已写入批注`
  return `只把引用 [${activeCite.value}] 的一句话结论写入文件副本`
})
function writeActive() {
  if (activeCite.value == null || !canWriteActive.value) return
  emit('annotate', activeCite.value)
}

function applyHighlights() {
  if (!host.value || !rendered) return
  clearCitationHighlights(host.value)
  navItems.value = []
  highlightMissed.value = false
  const list = props.highlights ?? []
  if (!list.length) {
    highlightMode.value = false
    return
  }
  const found = applyCitationHighlights(host.value, list, {
    pageSelector: mode.value === 'pdf' ? '.viewer-page .textLayer' : undefined,
  })
  navItems.value = found
  highlightMode.value = found.length > 0
  highlightMissed.value = found.length === 0
  const focus = props.focus
  const target = focus != null && found.includes(focus) ? focus : found[0]
  if (target != null) nextTick(() => gotoCitation(target))
}

watch(
  () => props.highlights,
  () => applyHighlights(),
  { deep: true },
)
watch(
  () => props.focus,
  (value) => {
    if (value != null) gotoCitation(value)
  },
)
 
function release() {
  void loading?.destroy()
  loading = null
  pdfDoc = null
  pageObserver?.disconnect()
  pageObserver = null
  window.clearTimeout(qualityTimer)
  try {
    pptxViewer?.destroy()
  } catch {
    // 渲染器自身清理失败不影响换文档
  }
  pptxViewer = null
}

onUnmounted(() => {
  alive = false
  release()
})
 
async function fetchFile() {
  const response = await fetch(`/api/projects/${props.projectId}/documents/${props.document.id}/file?preview=true`, {
    credentials: 'same-origin',
  })
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new ApiError(
      response.status,
      typeof data?.detail === 'string' ? data.detail : '原文件读取失败',
    )
  }
  // PPT/PPTX 有转换好的 PDF 时返回 application/pdf，否则返回原始文件类型。
  const type = (response.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase()
  return { data: await response.arrayBuffer(), type }
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
 
// 监听 scale 变化，重新应用到页面；位图不够清晰时升级可见页
watch(scale, () => {
  if (mode.value !== 'pdf') return
  applyScaleToPages()
  scheduleQualityUpgrade()
})
 
// ========== 清晰度控制 ==========
// canvas 位图倍率（相对页面基准尺寸）：位图像素 >= 显示尺寸 × DPR 才清晰。
// 缩放前固定 1 倍位图 + CSS 拉伸，放大后必然发糊，所以这里按需渲染。
let pdfDoc: pdfjsLib.PDFDocumentProxy | null = null
interface PageCanvas {
  canvas: HTMLCanvasElement
  quality: number // 当前位图倍率，0 表示尚未绘制
  painting: boolean
}
const pageCanvases: PageCanvas[] = []
const visiblePages = new Set<number>()
let qualityTimer: number | undefined
let pageObserver: IntersectionObserver | null = null

/** 页面越多，位图倍率上限越低，避免把内存吃满 */
function maxBitmapQuality(pages: number) {
  if (pages <= 40) return 3
  if (pages <= 120) return 2.5
  if (pages <= 400) return 2
  return 1.5
}

/** 当前显示所需的最低位图倍率 */
function neededQuality() {
  return scale.value * (window.devicePixelRatio || 1)
}

async function paintPage(index: number, quality: number) {
  if (!pdfDoc) return
  const entry = pageCanvases[index]
  if (!entry || entry.painting) return
  const target = Math.min(quality, maxBitmapQuality(pdfDoc.numPages))
  if (entry.quality >= target - 0.05) return
  entry.painting = true
  try {
    const page = await pdfDoc.getPage(index + 1)
    const vp = page.getViewport({ scale: target })
    entry.canvas.width = Math.max(1, Math.round(vp.width))
    entry.canvas.height = Math.max(1, Math.round(vp.height))
    await page.render({
      canvas: entry.canvas,
      canvasContext: entry.canvas.getContext('2d')!,
      viewport: vp,
    }).promise
    entry.quality = target
    page.cleanup()
  } finally {
    entry.painting = false
  }
  // 位图变了，CSS 尺寸要重新贴合当前缩放
  applyScaleToPages()
}

/** 缩放后：只把可见页升级到当前所需清晰度，其余等滚动进视野再补 */
function scheduleQualityUpgrade() {
  if (mode.value !== 'pdf' || !pdfDoc) return
  window.clearTimeout(qualityTimer)
  qualityTimer = window.setTimeout(() => {
    if (!pdfDoc) return
    const need = neededQuality()
    pageCanvases.forEach((entry, i) => {
      if (!visiblePages.has(i)) return
      if (entry.quality + 0.05 >= need) return
      void paintPage(i, need)
    })
  }, 200)
}

function observePages(host: HTMLElement) {
  pageObserver?.disconnect()
  visiblePages.clear()
  pageObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const index = Number((entry.target as HTMLElement).dataset.page)
        if (!Number.isFinite(index)) continue
        if (entry.isIntersecting) {
          visiblePages.add(index)
          const need = neededQuality()
          if ((pageCanvases[index]?.quality ?? 0) + 0.05 < need) {
            void paintPage(index, need)
          }
        } else {
          visiblePages.delete(index)
        }
      }
    },
    { root: host, rootMargin: '300px 0px' },
  )
  host.querySelectorAll<HTMLElement>('.viewer-page').forEach((box, i) => {
    box.dataset.page = String(i)
    pageObserver!.observe(box)
  })
}
 
async function renderPdf(data: ArrayBuffer, target: HTMLElement) {
  loading = pdfjsLib.getDocument({ data })
  const doc = await loading.promise
  pdfDoc = doc
  baseSizes.length = 0
  pageCanvases.length = 0

  // 先按容器宽度估算合适的初始清晰度，避免"先糊一下再变清晰"
  const dpr = window.devicePixelRatio || 1
  const probe = await doc.getPage(1)
  const probeVp = probe.getViewport({ scale: 1 })
  const fitEstimate = target.clientWidth ? (target.clientWidth - 24) / probeVp.width : 1
  probe.cleanup()
  const initialQuality = Math.max(
    1,
    Math.min(fitEstimate * dpr, maxBitmapQuality(doc.numPages)),
  )
 
  for (let number = 1; number <= doc.numPages; number++) {
    const page = await doc.getPage(number)
    const vp = page.getViewport({ scale: 1 })
    baseSizes.push({ width: vp.width, height: vp.height })
 
    const box = document.createElement('div')
    box.className = 'viewer-page'
 
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(vp.width * initialQuality))
    canvas.height = Math.max(1, Math.round(vp.height * initialQuality))
    box.appendChild(canvas)
    pageCanvases.push({ canvas, quality: 0, painting: false })
 
    const text = document.createElement('div')
    text.className = 'textLayer'
    box.appendChild(text)
 
    target.appendChild(box)
    if (!alive) return
 
    // 按估算清晰度直接渲染位图（位图像素 = 显示像素 × DPR，缩放后不糊）
    const renderVp = page.getViewport({ scale: initialQuality })
    await page.render({
      canvas,
      canvasContext: canvas.getContext('2d')!,
      viewport: renderVp,
    }).promise
    pageCanvases[number - 1].quality = initialQuality
    await new pdfjsLib.TextLayer({
      textContentSource: page.streamTextContent(),
      container: text,
      viewport: vp,
    }).render()
    page.cleanup()
  }

  // 记录可见页，滚动到尚未升级的页时补渲染
  observePages(target)
 
  // 初始自适应：按容器宽度算 scale
  nextTick(() => {
    if (!target.clientWidth || baseSizes.length === 0) return
    const fit = (target.clientWidth - 24) / baseSizes[0].width // 留 16px padding
    scale.value = Math.max(MIN_SCALE, Math.min(MAX_SCALE, Math.round(fit * 100) / 100))
    applyScaleToPages()
    scheduleQualityUpgrade()
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

// 备选方案：纯前端 PPTX 预览（pptx-preview），不依赖 LibreOffice / PowerPoint，
// 任何电脑打开网页都能看；版面还原度低于后端转的 PDF，所以只在拿不到 PDF 时启用。
let pptxViewer: { preview: (file: ArrayBuffer) => Promise<unknown>; destroy: () => void } | null = null

async function renderPptx(data: ArrayBuffer, target: HTMLElement) {
  const box = document.createElement('div')
  box.className = 'viewer-pptx'
  target.appendChild(box)
  const width = Math.max(320, Math.min(1400, target.clientWidth - 24 || 800))
  pptxViewer = initPptxPreview(box, { width })
  await pptxViewer.preview(data)
  scale.value = 1
}
 
// 最后兜底：没有 PDF（本机既无 LibreOffice 也无 PowerPoint）时展示解析出的文字。
// 文字由后端在解析阶段保存，前端只做渲染，无需安装任何额外组件。
async function renderText(target: HTMLElement) {
  let content: string | null = null
  try {
    const response = await fetch(
      `/api/projects/${props.projectId}/documents/${props.document.id}/content`,
      { credentials: 'same-origin' },
    )
    if (response.ok) {
      content = ((await response.json()) as { content: string }).content
    }
  } catch {
    content = null
  }
  if (!alive) return

  const box = document.createElement('div')
  if (content === null) {
    box.className = 'viewer-hint viewer-hint-inline'
    box.textContent = '未生成页面预览（本机未安装 LibreOffice 或 PowerPoint），且该文档没有保存解析正文，请下载原文件查看'
    target.appendChild(box)
    return
  }
  box.className = 'viewer-text markdown'
  box.innerHTML = DOMPurify.sanitize(marked.parse(content, { async: false }) as string)
  target.appendChild(box)
  scale.value = 1
}

// 监听容器宽度变化 → PDF 自动重新 fit
let ro: ResizeObserver | null = null
onMounted(() => {
  if (!host.value) return
  ro = new ResizeObserver(() => {
    if (!host.value || mode.value !== 'pdf' || baseSizes.length === 0) return
    // 只在接近 fit 状态时（用户没主动 zoom 过）自动调整
    const fit = (host.value.clientWidth - 24) / baseSizes[0].width
    const diff = Math.abs(fit - scale.value) / fit
    if (diff > 0.15) {
      // 差距大 → 重新 fit
      scale.value = Math.max(MIN_SCALE, Math.min(MAX_SCALE, Math.round(fit * 100) / 100))
      applyScaleToPages()
      scheduleQualityUpgrade()
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
  release() // 释放上一份文档的 PDF 对象、观察器与定时器
  baseSizes.length = 0
  pageCanvases.length = 0
  visiblePages.clear()
  rendered = false
  clearHighlights()
  try {
    const { data, type } = await fetchFile()
    if (!alive || !host.value) return
    host.value.innerHTML = ''
    if (renderExt.value === 'pdf') {
      if (type === 'application/pdf') {
        mode.value = 'pdf'
        await renderPdf(data, host.value)
      } else if (['ppt', 'pptx'].includes(ext.value)) {
        // 拿不到 PDF（本机无 LibreOffice 也无 PowerPoint）时的降级链：
        // 纯前端 pptx-preview（不需要任何本机部件）→ 解析文字兜底。
        // .ppt 老格式 pptx-preview 不支持，直接走文字兜底。
        if (ext.value === 'pptx') {
          try {
            mode.value = 'pptx'
            await renderPptx(data, host.value)
          } catch {
            host.value.innerHTML = ''
            mode.value = 'text'
            await renderText(host.value)
          }
        } else {
          mode.value = 'text'
          await renderText(host.value)
        }
      } else {
        throw new ApiError(500, '预览文件内容异常')
      }
    } else {
      mode.value = 'docx'
      await renderDocx(data, host.value)
    }
    rendered = true
    await nextTick()
    applyHighlights()
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
  scheduleQualityUpgrade()
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
          v-if="props.highlights?.length"
          class="toolbar-action is-primary"
          :disabled="props.annotating"
          title="下载带 AI 批注的文件副本（需要先给某条引用加入批注）"
          @click="emit('downloadAnnotated')"
        >
          <ArrowDownToLine :size="13" /><span>带批注版本</span>
        </button>
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
    <div v-if="highlightMode && navItems.length" class="cite-nav">
      <span class="cite-nav-label"><Highlighter :size="13" />引用定位</span>
      <button
        v-for="item in navItems"
        :key="item"
        class="cite-nav-btn"
        :class="{ active: activeCite === item, 'is-noted': annotatedSet.has(item) }"
        :title="
          annotatedSet.has(item) ? `跳到引用 [${item}]（已写入批注）` : `跳到引用 [${item}]`
        "
        @click="gotoCitation(item)"
      >
        {{ item }}
      </button>
      <button
        class="cite-nav-write"
        :disabled="!canWriteActive"
        :title="writeTitle"
        @click="writeActive"
      >
        <StickyNote :size="12" />写入批注
      </button>
      <button class="cite-nav-close" title="关闭全部高亮" @click="clearHighlights">关闭高亮</button>
    </div>
    <div v-if="busy" class="viewer-hint">正在加载原文件…</div>
    <div v-if="highlightMissed" class="cite-nav cite-nav-missed">
      <span class="cite-nav-label"><Highlighter :size="13" />引用原文未能在此预览中定位，可下载原文件查看</span>
    </div>
    <div
      v-show="!busy"
      ref="host"
      class="viewer-host"
      :class="{ 'pan-mode': panMode }"
      :style="mode === 'docx' || mode === 'text' || mode === 'pptx' ? { zoom: String(scale) } : {}"
      @mousedown="onHostMouseDown"
    ></div>
  </section>
</template>