<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUp,
  BookOpen,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CircleHelp,
  FileText,
  Folder,
  FolderOpen,
  GraduationCap,
  Layers3,
  LoaderCircle,
  LogOut,
  Menu,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Search,
  Send,
  Settings2,
  Sparkles,
  Square,
  Trash2,
  Upload,
  X,
} from 'lucide-vue-next'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import SchoolPanel from './components/SchoolPanel.vue'
import SavedTimetable from './components/SavedTimetable.vue'
import ProjectActivity from './components/ProjectActivity.vue'
import DocumentActions from './components/DocumentActions.vue'
import DocumentViewer from './components/DocumentViewer.vue'
import MISTimetable from './components/MISTimetable.vue'
import {
  ApiError,
  addAnnotation,
  annotatedFileUrl,
  post,
  request,
  streamAsk,
  type Answer,
  type PendingAction,
  type CitationHighlight,
  type Course,
  type Document,
  type Project,
  type Source,
  type User,
  type Job,
  type PageResult,
  type HistoryMessage,
} from './api'
 
interface NoteState {
  busy?: boolean
  error?: string
  total?: number
  url?: string
  filename?: string
}
interface Turn {
  question: string
  result?: Answer
  error?: string
  jobId?: string
  /** 用户中途点了“停止”，这一轮不再等待回答 */
  stopped?: boolean
  /** 每条引用的文件批注状态，key 是引用编号 */
  notes?: Record<number, NoteState>
}
interface Chat {
  turns: Turn[]
  conversationId?: number
}
const user = ref<User | null>(null)
const booting = ref(true)
const projects = ref<Project[]>([])
const activeId = ref<number | null>(null)
const openProjectPicker = ref(false)
const documents = ref<Document[]>([])
const selectedIds = ref<number[]>([])
const viewerDoc = ref<Document | null>(null)
const chats = ref<Record<number, Chat>>({})
// 无项目的默认对话用保留键 0（真实项目 id 都 > 0）。activeId 为 null 即默认窗口。
const DEFAULT_KEY = 0
const view = ref<'research' | 'timetable' | 'school'>('research')
const activityOpen = ref(false)
const historyLoading = ref(false)
const agentImage = ref<{ id: string; name: string } | null>(null)
const agentImageUploading = ref(false)
const agentImageInput = ref<HTMLInputElement>()
async function attachAgentImage(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file || agentImageUploading.value) return
  if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 10 * 1024 * 1024) {
    error.value = '请选择不超过 10 MB 的 PNG/JPEG 课表图片。'
    return
  }
  const version = sessionVersion
  agentImageUploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const result = await request<{ image_id: string }>('/timetable/attachments', { method: 'POST', body: form })
    if (version === sessionVersion) {
      agentImage.value = { id: result.image_id, name: file.name }
      if (!query.value.trim()) query.value = '请识别这张图片，补充到我的课表'
    }
  } catch (e) { if (version === sessionVersion) report(e) }
  finally {
    agentImageUploading.value = false
    if (agentImageInput.value) agentImageInput.value.value = ''
  }
}
const durableAsk = ref(false)
const savedTimetable = ref<InstanceType<typeof SavedTimetable>>()
let historyRequest = 0
let askTimer: ReturnType<typeof setTimeout> | undefined
let wakeAsk: (() => void) | undefined
/** 当前这轮问答的中止器：用户点“停止”时中断请求 */
let askController: AbortController | null = null
let askInterrupted = false
/** 轮次序号：连点停止/重新提问时，避免旧请求回来把新状态覆盖掉 */
let askSeq = 0
/** 正在等待回答的那一轮，供“停止”立刻打标记 */
let askingTurn: { chat: Chat; index: number } | null = null
const filter = ref('')
const query = ref('')
const asking = ref(false)
const uploading = ref(false)
const recognizing = ref(false)
const loadingDocs = ref(false)
const projectLoading = ref(false)
const sidebarOpen = ref(false)
const mobileDocs = ref(false)
 
// 两侧栏可折叠 + 可拖拽调宽（桌面端）
const sidebarCollapsed = ref(false)
const documentsCollapsed = ref(false)
const sidebarWidth = ref(244)
const documentsWidth = ref(294)
const MIN_SIDEBAR = 160, MAX_SIDEBAR = 360
const MIN_DOCS = 220, MAX_DOCS = 720
 
function applyLayoutVars() {
  document.documentElement.style.setProperty(
    '--sidebar-w',
    sidebarCollapsed.value ? '0px' : `${sidebarWidth.value}px`,
  )
  document.documentElement.style.setProperty(
    '--sidebar-w-px',
    `${sidebarWidth.value}px`,
  )
  document.documentElement.style.setProperty(
    '--docs-w',
    documentsCollapsed.value ? '0px' : `${documentsWidth.value}px`,
  )
  document.documentElement.style.setProperty(
    '--docs-w-px',
    `${documentsWidth.value}px`,
  )
}
function persistLayout() {
  try {
    localStorage.setItem(
      'aa.layout',
      JSON.stringify({
        sidebarCollapsed: sidebarCollapsed.value,
        documentsCollapsed: documentsCollapsed.value,
        sidebarWidth: sidebarWidth.value,
        documentsWidth: documentsWidth.value,
      }),
    )
  } catch {}
}
function loadLayout() {
  try {
    const raw = localStorage.getItem('aa.layout')
    if (raw) {
      const data = JSON.parse(raw)
      if (typeof data.sidebarCollapsed === 'boolean') sidebarCollapsed.value = data.sidebarCollapsed
      if (typeof data.documentsCollapsed === 'boolean') documentsCollapsed.value = data.documentsCollapsed
      if (typeof data.sidebarWidth === 'number')
        sidebarWidth.value = Math.min(MAX_SIDEBAR, Math.max(MIN_SIDEBAR, data.sidebarWidth))
      if (typeof data.documentsWidth === 'number')
        documentsWidth.value = Math.min(MAX_DOCS, Math.max(MIN_DOCS, data.documentsWidth))
    }
  } catch {}
}
loadLayout()
applyLayoutVars()
watch([sidebarCollapsed, documentsCollapsed, sidebarWidth, documentsWidth], () => {
  applyLayoutVars()
  persistLayout()
})
 
// 拖拽调整宽度
let dragSide: 'left' | 'right' | null = null
let startX = 0, startWidth = 0
function startDrag(side: 'left' | 'right', e: MouseEvent) {
  e.preventDefault()
  dragSide = side
  startX = e.clientX
  startWidth = side === 'left' ? sidebarWidth.value : documentsWidth.value
  document.body.style.cursor = side === 'left' ? 'col-resize' : 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', endDrag)
}
function onDrag(e: MouseEvent) {
  if (!dragSide) return
  if (dragSide === 'left') {
    const next = Math.min(MAX_SIDEBAR, Math.max(MIN_SIDEBAR, startWidth + (e.clientX - startX)))
    sidebarWidth.value = next
  } else {
    const next = Math.min(MAX_DOCS, Math.max(MIN_DOCS, startWidth - (e.clientX - startX)))
    documentsWidth.value = next
  }
}
function endDrag() {
  dragSide = null
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', endDrag)
}
onUnmounted(() => endDrag())
const error = ref('')
const notice = ref('')
let errorTimer: ReturnType<typeof setTimeout> | undefined
let noticeTimer: ReturnType<typeof setTimeout> | undefined
// Toast 自动消失：错误停留更久，普通提示短一些。
watch(error, (value) => {
  clearTimeout(errorTimer)
  if (value) errorTimer = setTimeout(() => (error.value = ''), 8000)
})
watch(notice, (value) => {
  clearTimeout(noticeTimer)
  if (value) noticeTimer = setTimeout(() => (notice.value = ''), 4000)
})
onUnmounted(() => {
  clearTimeout(errorTimer)
  clearTimeout(noticeTimer)
})
const connection = ref<'connecting' | 'online' | 'offline'>('connecting')
const modal = ref<'login' | 'register' | 'reset' | 'project' | 'help' | 'delete-project' | null>(null)
const dialog = ref<HTMLDialogElement>()
const modalError = ref('')
const modalBusy = ref(false)
const account = ref('')
const password = ref('')
const emailCode = ref('')
const confirmPassword = ref('')
const codeBusy = ref(false)
const codeNotice = ref('')
const codeSentAt = ref(0)
async function sendEmailCode() {
  if (codeBusy.value || modalBusy.value) return
  if (Date.now() - codeSentAt.value < 60000) {
    codeNotice.value = '请等待 60 秒后再发送。'
    return
  }
  codeBusy.value = true
  modalError.value = ''
  try {
    const result = await post<{ message: string }>('/user/email-code', {
      email: account.value.trim(),
      purpose: modal.value === 'reset' ? 'reset' : 'register',
    })
    codeNotice.value = result.message
    codeSentAt.value = Date.now()
  } catch (e) {
    modalError.value = e instanceof Error ? e.message : '验证码发送失败'
  } finally {
    codeBusy.value = false
  }
}
const username = ref('')
const phone = ref('')
const projectName = ref('')
const uploadInput = ref<HTMLInputElement>()
const imageInput = ref<HTMLInputElement>()
const composer = ref<HTMLTextAreaElement>()
const messagesEnd = ref<HTMLElement>()
const dragging = ref(false)
const settingsOpen = ref(false)
const scopeOpen = ref(false) // 输入框左下角"资料范围"小弹窗
const recall = ref(40)
const excerpt = ref('')
const excerptDocument = ref<number | null>(null)
// AI 回答划词（方案 B）：一次性引用，发送后清空；浮动「追问」按钮用视口坐标定位。
const quoted = ref<string | null>(null)
const quoteBtn = ref<{ text: string; x: number; y: number } | null>(null)
const source = ref<{ source: Source; text: string } | null>(null)
const courses = ref<Course[]>([])
const timetableFile = ref<File | null>(null)
const timetablePreview = ref('')
let pollTimer: ReturnType<typeof setTimeout> | undefined
let docRequest = 0
let sessionVersion = 0
 
const activeProject = computed(() => projects.value.find((p) => p.id === activeId.value))
const visibleProjects = computed(() =>
  projects.value.filter((p) => p.name.toLowerCase().includes(filter.value.toLowerCase())),
)
const readyDocs = computed(() => documents.value.filter((d) => d.status === 'completed'))
const excerptDocName = computed(
  () => documents.value.find((d) => d.id === excerptDocument.value)?.filename ?? '原文',
)
const processingCount = computed(
  () => documents.value.filter((d) => ['queued', 'processing'].includes(d.status)).length,
)
const currentChat = computed(() => chats.value[activeId.value ?? DEFAULT_KEY])
const anyBusy = computed(
  () => asking.value || uploading.value || recognizing.value || modalBusy.value,
)
const canAsk = computed(
  () =>
    !!user.value &&
    !!query.value.trim() &&
    !historyLoading.value &&
    !asking.value &&
    !agentImageUploading.value &&
    (!agentImage.value || (!selectedIds.value.length && !excerptDocument.value)) &&
    (excerptDocument.value === null || !!excerpt.value.trim()),
)
const days = [
  ['Mon', '周一'],
  ['Tue', '周二'],
  ['Wed', '周三'],
  ['Thu', '周四'],
  ['Fri', '周五'],
  ['Sat', '周六'],
  ['Sun', '周日'],
]
const suggestions = [
  {
    icon: FileText,
    title: '梳理文献脉络',
    text: '总结研究问题、方法与核心结论',
    query: '请总结这些文献的研究问题、研究方法和核心结论，并标注来源。',
  },
  {
    icon: Layers3,
    title: '对比不同观点',
    text: '发现文献之间的联系与差异',
    query: '请对比这些文献的主要观点、共同点和差异，并说明各自的依据。',
  },
  {
    icon: Sparkles,
    title: '深入理解概念',
    text: '把复杂知识拆解得更清晰',
    query: '请提取这些资料中的关键概念，用通俗的语言解释，并给出原文依据。',
  },
]
const statusText = (status: string) =>
  ({ queued: '排队中', processing: '解析中', completed: '已就绪', failed: '处理失败，可重试' })[
    status
  ] || status
const markdown = (text: string) =>
  DOMPurify.sanitize(marked.parse(text, { async: false }) as string)
function openModal(value: typeof modal.value) {
  if (codeBusy.value) return
  password.value = ''
  emailCode.value = ''
  confirmPassword.value = ''
  codeNotice.value = ''
  modalError.value = ''
  modal.value = value
}
watch(modal, async (value) => {
  await nextTick()
  if (value && dialog.value && !dialog.value.open) dialog.value.showModal()
  if (!value) {
    dialog.value?.close()
    password.value = ''
    projectToDelete.value = null
    modalError.value = ''
  }
})
function closeModal() {
  if (!modalBusy.value && !codeBusy.value) modal.value = null
}
function changeView(nextView: typeof view.value) {
  view.value = nextView
  sidebarOpen.value = false
}
function editQuestion(question: string) {
  query.value = question
  composer.value?.focus()
}
// 预览器里的引用高亮：打开文档时传入，关闭文档时清空
const viewerHighlights = ref<CitationHighlight[]>([])
const viewerFocus = ref<number | null>(null)
// 打开预览的那轮回答：预览器里的“写入批注/下载”要靠它拿到引用原文
const viewerTurn = ref<Turn | null>(null)
// 预览器顶栏“写入批注/下载”进行中
const annotating = ref(false)
// 当前预览里哪些引用已经写入过批注（逐条标记）
const viewerAnnotatedIndices = computed(() => {
  const turn = viewerTurn.value
  if (!turn?.notes) return []
  return viewerHighlights.value.filter((h) => turn.notes?.[h.index]?.url).map((h) => h.index)
})
// 点引用打开原文时，右栏至少要有这么宽才够阅读
const VIEWER_MIN_WIDTH = 540

function highlightOf(source: Source, text: string): CitationHighlight {
  // 定位精度：命中的子块 > 回答引文 > 整块父内容（父块太长，开头内容会把名额占光）
  const text_ = source.child_content || source.quote_text || text
  return { index: source.index, text: text_, pages: source.page_numbers ?? [] }
}

function openCitation(
  citation: Source,
  _text?: string,
  highlights?: CitationHighlight[],
  turn?: Turn,
) {
  const doc = documents.value.find((d) => d.id === citation.document_id)
  // 右栏收起或太窄时，自动展开到适合阅读原文的宽度
  if (documentsCollapsed.value) documentsCollapsed.value = false
  if (documentsWidth.value < VIEWER_MIN_WIDTH)
    documentsWidth.value = Math.min(MAX_DOCS, VIEWER_MIN_WIDTH)
  viewerHighlights.value =
    highlights ?? (_text ? [highlightOf(citation, _text)] : [])
  viewerFocus.value = citation.index
  viewerTurn.value = turn ?? null
  if (doc) {
    viewerDoc.value = doc
    mobileDocs.value = true
  } else {
    // 回退：文档不在当前列表里时，用旧的纯文字切片详情
    source.value = { source: citation, text: _text || '未返回原文片段' }
    mobileDocs.value = true
  }
}
interface CitationGroup {
  filename: string
  items: { citation: Source; text: string }[]
}
function citationGroups(turn: Turn): CitationGroup[] {
  const result = turn.result
  if (!result) return []
  const groups = new Map<number, CitationGroup>()
  result.sources.forEach((citation, i) => {
    const text = result.retrieved_contexts[i] || ''
    let group = groups.get(citation.document_id)
    if (!group) {
      group = { filename: citation.filename, items: [] }
      groups.set(citation.document_id, group)
    }
    group.items.push({ citation, text })
  })
  return [...groups.values()]
}
function openCitationGroup(turn: Turn, group: CitationGroup) {
  // 同一文档的多条引用一起高亮，焦点放在第一条
  const highlights = group.items.map((it) => highlightOf(it.citation, it.text))
  openCitation(group.items[0].citation, group.items[0].text || '未返回原文片段', highlights, turn)
}

/** 单轮回答指纹：同一轮重复点击复用结论；新一轮（问题或回答变了）覆盖旧批注 */
function turnFingerprint(turn: Turn): string {
  const raw = `${turn.question}\n${turn.result?.answer ?? ''}`
  let hash = 0x811c9dc5
  for (let i = 0; i < raw.length; i++) {
    hash ^= raw.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return `${hash.toString(16)}${raw.length.toString(16)}`
}

/** 把这一条引用的一句话结论写进文件副本（Word 批注 / PDF 高亮 / PPT 备注） */
async function writeAnnotation(turn: Turn, item: { citation: Source; text: string }) {
  const projectId = activeId.value
  const result = turn.result
  if (!projectId || !result) return
  const citeIndex = item.citation.index
  const current = turn.notes?.[citeIndex]
  if (current?.busy || current?.url) return
  if (!turn.notes) turn.notes = {}
  turn.notes[citeIndex] = { busy: true }
  try {
    const anchor = item.citation.child_content || item.citation.quote_text || item.text
    const saved = await addAnnotation(projectId, item.citation.document_id, {
      cite_index: citeIndex,
      anchor_text: anchor || item.citation.filename,
      question: turn.question,
      answer: result.answer,
      pages: item.citation.page_numbers ?? [],
      turn_key: turnFingerprint(turn),
    })
    turn.notes[citeIndex] = {
      total: saved.total,
      url: annotatedFileUrl(projectId, item.citation.document_id),
      filename: saved.filename,
    }
    notice.value = `已写入 AI 批注（该文件共 ${saved.total} 条）：${saved.filename}`
  } catch (error) {
    const message = error instanceof Error ? error.message : '写入批注失败'
    turn.notes[citeIndex] = { error: message }
    notice.value = message
  }
}

/** 预览器里点某条引用旁的便签：只把这一条写进文件副本 */
async function annotateInViewer(index: number) {
  const turn = viewerTurn.value
  const result = turn?.result
  if (!turn || !result || annotating.value) return
  const position = result.sources.findIndex((s) => s.index === index)
  if (position < 0) return
  annotating.value = true
  try {
    await writeAnnotation(turn, {
      citation: result.sources[position],
      text: result.retrieved_contexts[position] || '',
    })
  } finally {
    annotating.value = false
  }
}

/** 预览器顶栏的「带批注版本」：直接下载已生成的副本，没有副本时给出提示 */
async function downloadAnnotatedCopy() {
  const projectId = activeId.value
  const doc = viewerDoc.value
  if (!projectId || !doc || annotating.value) return
  try {
    const response = await fetch(annotatedFileUrl(projectId, doc.id), { credentials: 'include' })
    if (!response.ok) {
      notice.value =
        response.status === 404
          ? '这份文件还没有 AI 批注副本，请先点引用编号旁的便签按钮加入批注'
          : `下载失败（${response.status}）`
      return
    }
    const blob = await response.blob()
    const disposition = response.headers.get('Content-Disposition') || ''
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
    const filename = encoded
      ? decodeURIComponent(encoded[1])
      : `${doc.filename.replace(/\.[^.]+$/, '')}_AI批注${doc.filename.match(/\.[^.]+$/)?.[0] ?? ''}`
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
    notice.value = `已开始下载：${filename}`
  } catch (error) {
    notice.value = error instanceof Error ? error.message : '下载带批注版本失败'
  }
}

/** 回答里点 [3] 这样的引用角标：打开对应文档并聚焦该引用 */
function onAnswerClick(event: MouseEvent, turn: Turn) {
  const el = (event.target as HTMLElement | null)?.closest('.cite-ref') as HTMLElement | null
  if (!el) return
  const index = Number(el.dataset.cite)
  const result = turn.result
  if (!result || !Number.isFinite(index)) return
  const pos = result.sources.findIndex((s) => s.index === index)
  if (pos < 0) return
  const citation = result.sources[pos]
  const text = result.retrieved_contexts[pos] || ''
  const highlights = result.sources
    .map((s, i) => ({ s, text: result.retrieved_contexts[i] || '' }))
    .filter((x) => x.s.document_id === citation.document_id && x.text)
    .map((x) => highlightOf(x.s, x.text))
  openCitation(citation, text, highlights.length ? highlights : undefined, turn)
}

/** 在 AI 回答里划词：选区落在当前回答内时，弹出浮动「追问」按钮 */
function onAnswerSelect(event: MouseEvent) {
  const sel = window.getSelection()
  const text = sel?.toString().trim()
  const anchor = event.currentTarget as HTMLElement
  if (!sel || !text || !anchor.contains(sel.anchorNode) || !anchor.contains(sel.focusNode)) {
    quoteBtn.value = null
    return
  }
  const rect = sel.getRangeAt(0).getBoundingClientRect()
  quoteBtn.value = { text: text.slice(0, 10000), x: rect.left + rect.width / 2, y: rect.top }
}
/** 点浮动按钮：把选中的 AI 原话存成一次性引用（不收选区会自己被清） */
function pickQuote() {
  if (!quoteBtn.value) return
  quoted.value = quoteBtn.value.text
  clearExcerpt() // AI 回答划词与原文划词互斥
  quoteBtn.value = null
  window.getSelection()?.removeAllRanges()
}
/** 预览里划词：走现成的 document_id + selected_text 划线问答契约 */
function onViewerQuote(text: string) {
  if (!viewerDoc.value) return
  excerptDocument.value = viewerDoc.value.id
  excerpt.value = text
  quoted.value = null // 与 AI 回答划词互斥
}
function clearExcerpt() {
  excerptDocument.value = null
  excerpt.value = ''
}
/** 开始新选区 / 滚动时，收起已弹出的浮动按钮（定位会失效） */
function hideQuoteBtn() {
  quoteBtn.value = null
}
onMounted(() => {
  window.addEventListener('mousedown', hideQuoteBtn)
  window.addEventListener('scroll', hideQuoteBtn, true)
})
onUnmounted(() => {
  window.removeEventListener('mousedown', hideQuoteBtn)
  window.removeEventListener('scroll', hideQuoteBtn, true)
})

/** 把回答中的 [n] / 【n】 引用角标替换成可点击按钮（跳过 HTML 标签与代码块内部） */
function answerHtml(text: string) {
  return markdown(text)
    .split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>)/i)
    .map((part) => {
      if (/^<(pre|code)\b/i.test(part)) return part
      return part.replace(
        /(<[^>]*>)|[[【]\s*(\d{1,2})\s*[\]】]/g,
        (match, tag: string | undefined, num: string | undefined) =>
          tag ??
          `<button type="button" class="cite-ref" data-cite="${num}" title="查看引用 ${num}">${num}</button>`,
      )
    })
    .join('')
}
/** 从资料列表直接打开文档（不带引用高亮，避免残留上一次的高亮） */
function openDoc(doc: Document) {
  viewerHighlights.value = []
  viewerFocus.value = null
  viewerDoc.value = doc
}
watch(viewerDoc, (doc) => {
  if (!doc) {
    viewerHighlights.value = []
    viewerFocus.value = null
  }
})
const activeTurn = ref(0)
/** 对话导航圆点：每页 5 个，可上下翻组，避免长对话把圆点排成一长串 */
const DOT_PAGE_SIZE = 5
const dotPage = ref(0)
const dotPageCount = computed(() =>
  Math.max(1, Math.ceil((currentChat.value?.turns.length ?? 0) / DOT_PAGE_SIZE)),
)
const dotTurns = computed(() => {
  const turns = currentChat.value?.turns ?? []
  const start = dotPage.value * DOT_PAGE_SIZE
  return turns.slice(start, start + DOT_PAGE_SIZE).map((turn, i) => ({ turn, index: start + i }))
})
function shiftDotPage(delta: number) {
  const next = dotPage.value + delta
  if (next < 0 || next >= dotPageCount.value) return
  dotPage.value = next
}
watch(activeTurn, (value) => {
  const page = Math.floor(value / DOT_PAGE_SIZE)
  if (page !== dotPage.value && page < dotPageCount.value) dotPage.value = page
})
function turnPreview(question: string) {
  const text = question.replace(/\s+/g, ' ').trim()
  return text.length > 20 ? text.slice(0, 20) + '…' : text
}
function scrollToTurn(index: number) {
  activeTurn.value = index
  document.getElementById(`turn-${index}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function onChatScroll(e: Event) {
  const container = e.target as HTMLElement
  const containerTop = container.getBoundingClientRect().top
  const count = currentChat.value?.turns.length ?? 0
  let current = 0
  for (let i = 0; i < count; i++) {
    const el = document.getElementById(`turn-${i}`)
    if (el && el.getBoundingClientRect().top - containerTop <= 80) current = i
  }
  activeTurn.value = current
}
function askFromSource() {
  if (!source.value) return
  excerptDocument.value = source.value.source.document_id
  excerpt.value = source.value.text.slice(0, 10000)
  settingsOpen.value = true
  source.value = null
  mobileDocs.value = false
}
function report(e: unknown) {
  const message = e instanceof Error ? e.message : '操作失败，请重试。'
  error.value = message
  if (e instanceof ApiError && e.status === 401) {
    resetSession()
    openModal('login')
    modalError.value = '登录已过期，请重新登录。'
  }
}
function resetSession() {
  sessionVersion++
  historyRequest++
  clearTimeout(askTimer)
  wakeAsk?.()
  asking.value = false
  activityOpen.value = false
  error.value = ''
  notice.value = ''
  agentImage.value = null
  docRequest++
  clearTimeout(pollTimer)
  user.value = null
  projects.value = []
  documents.value = []
  activeId.value = null
  chats.value = {}
  selectedIds.value = []
  scopeOpen.value = false
  courses.value = []
  source.value = null
  query.value = ''
  excerpt.value = ''
  excerptDocument.value = null
  timetableFile.value = null
  if (timetablePreview.value) URL.revokeObjectURL(timetablePreview.value)
  timetablePreview.value = ''
}
async function loadProjects() {
  projectLoading.value = true
  try {
    projects.value = await request<Project[]>('/projects')
    // 登录后默认停在无项目的默认对话：初始化并恢复最近一段默认会话。
    chats.value[DEFAULT_KEY] ??= { turns: [] }
    if (!chats.value[DEFAULT_KEY].turns.length) void loadLatestConversation(null)
  } finally {
    projectLoading.value = false
  }
}
async function bootstrap() {
  booting.value = true
  error.value = ''
  connection.value = 'connecting'
  try {
    user.value = await request<User>('/user/me')
    connection.value = 'online'
    await loadProjects()
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) connection.value = 'online'
    else {
      connection.value = 'offline'
      report(e)
    }
  } finally {
    booting.value = false
  }
}
async function authenticate() {
  modalBusy.value = true
  modalError.value = ''
  try {
    if (modal.value !== 'login' && password.value !== confirmPassword.value) {
      throw new Error('两次输入的密码不一致')
    }
    if (modal.value === 'reset') {
      await post('/user/reset-password', {
        email: account.value.trim(),
        code: emailCode.value,
        password: password.value,
      })
      notice.value = '密码已重置，请使用新密码登录。'
      openModal('login')
      password.value = ''
      return
    }
    if (modal.value === 'register') {
      await post('/user/create', {
        username: username.value.trim(),
        phone: phone.value.trim() || null,
        password: password.value,
        email: account.value.trim(),
        code: emailCode.value,
      })
      notice.value = '注册成功，请使用刚才的账号登录。'
      openModal('login')
      password.value = ''
      return
    }
    await post('/user/login', { account: account.value.trim(), password: password.value })
    user.value = await request<User>('/user/me')
    connection.value = 'online'
    modal.value = null
    error.value = ''
    notice.value = ''
    await loadProjects()
  } catch (e) {
    modalError.value = e instanceof Error ? e.message : '登录失败'
    if (!modal.value) report(e)
  } finally {
    modalBusy.value = false
  }
}
async function logout() {
  try {
    await post('/user/logout', {})
    resetSession()
    notice.value = '已退出登录。'
  } catch (e) {
    report(e)
  }
}
function newProject() {
  projectName.value = ''
  openModal(user.value ? 'project' : 'login')
}
async function createProject() {
  if (!projectName.value.trim()) return
  modalBusy.value = true
  modalError.value = ''
  try {
    const result = await post<{ project_id: number }>('/projects', {
      name: projectName.value.trim(),
    })
    projects.value.unshift({ id: result.project_id, name: projectName.value.trim() })
    activeId.value = result.project_id
    modal.value = null
    sidebarOpen.value = false
    view.value = 'research'
    notice.value = '项目已创建，上传资料开始研究。'
  } catch (e) {
    modalError.value = e instanceof Error ? e.message : '创建失败'
    if (e instanceof ApiError && e.status === 401) report(e)
  } finally {
    modalBusy.value = false
  }
}
async function loadDocuments(id: number) {
  clearTimeout(pollTimer)
  const sequence = ++docRequest
  try {
    const prevProcessing = new Set(
      documents.value.filter((d) => ['queued', 'processing'].includes(d.status)).map((d) => d.id),
    )
    const result = await request<Document[]>(`/projects/${id}/documents`)
    if (sequence !== docRequest || id !== activeId.value) return
    const newlyCompleted = result.some(
      (d) => d.status === 'completed' && prevProcessing.has(d.id),
    )
    documents.value = result
    if (newlyCompleted) playDing()
    selectedIds.value = selectedIds.value.filter((selected) =>
      result.some((d) => d.id === selected && d.status === 'completed'),
    )
    if (
      excerptDocument.value &&
      !result.some((d) => d.id === excerptDocument.value && d.status === 'completed')
    ) {
      excerptDocument.value = null
      excerpt.value = ''
    }
    if (result.some((d) => ['queued', 'processing'].includes(d.status)))
      pollTimer = setTimeout(() => void loadDocuments(id), 3500)
  } catch (e) {
    if (sequence === docRequest) report(e)
  } finally {
    if (sequence === docRequest) loadingDocs.value = false
  }
}
watch(activeId, (id) => {
  historyRequest++
  historyLoading.value = false
  activityOpen.value = false
  activeTurn.value = 0
  dotPage.value = 0
  clearTimeout(pollTimer)
  docRequest++
  documents.value = []
  selectedIds.value = []
  source.value = null
  query.value = ''
  excerpt.value = ''
  excerptDocument.value = null
  loadingDocs.value = !!id
  if (id) {
    chats.value[id] ??= { turns: [] }
    void loadDocuments(id)
    if (!chats.value[id].turns.length) void loadLatestConversation(id)
  } else {
    // 默认窗口（无项目）：只恢复历史，不加载文档。
    chats.value[DEFAULT_KEY] ??= { turns: [] }
    if (!chats.value[DEFAULT_KEY].turns.length) void loadLatestConversation(null)
  }
})
function chooseProject(id: number) {
  activeId.value = id
  view.value = 'research'
  sidebarOpen.value = false
}
/** 回到无项目的默认对话（AI 助手：工具 + 直接问答，不检索资料）。 */
function openDefaultChat() {
  view.value = 'research'
  activeId.value = null
  sidebarOpen.value = false
}
/** 删除项目：点垃圾桶先弹确认窗口，确认后才真的删 */
const projectToDelete = ref<Project | null>(null)
function askDeleteProject(project: Project) {
  projectToDelete.value = project
  openModal('delete-project')
}
function removeProject() {
  const project = projectToDelete.value
  if (!project || modalBusy.value) return
  modalBusy.value = true
  modalError.value = ''
  void (async () => {
    try {
      await request(`/projects/${project.id}`, { method: 'DELETE' })
      const remaining = projects.value.filter((item) => item.id !== project.id)
      projects.value = remaining
      delete chats.value[project.id]
      if (activeId.value === project.id) {
        activeId.value = null
        if (remaining.length) chooseProject(remaining[0].id)
      }
      projectToDelete.value = null
      modalBusy.value = false
      closeModal()
    } catch (e) {
      modalError.value = e instanceof Error ? e.message : '删除失败，请重试。'
      if (e instanceof ApiError && e.status === 401) report(e)
    } finally {
      modalBusy.value = false
    }
  })()
}
async function upload(files: File[]) {
  if (!activeId.value || uploading.value) return
  error.value = ''
  notice.value = ''
  const id = activeId.value
  const valid = files.filter((f) => /\.(pdf|txt|docx|pptx)$/i.test(f.name) && f.size > 0)
  const invalid = files.filter((f) => !valid.includes(f))
  if (invalid.length)
    error.value = `未上传：${invalid.map((f) => f.name).join('、')}。请选择非空的 PDF、TXT、DOCX 或 PPTX 文件。`
  if (!valid.length) return
  const form = new FormData()
  valid.forEach((f) => form.append('files', f))
  uploading.value = true
  try {
    const result = await request<{ saved_files: Document[]; failed_files: string[] }>(
      `/projects/${id}/documents`,
      { method: 'POST', body: form },
    )
    notice.value = `已接收 ${result.saved_files.length} 份资料，解析完成后即可提问。`
    if (result.failed_files.length)
      error.value = `部分文件未上传：${result.failed_files.join('、')}`
    if (activeId.value === id) await loadDocuments(id)
  } catch (e) {
    report(e)
  } finally {
    uploading.value = false
    if (uploadInput.value) uploadInput.value.value = ''
  }
}
function onFiles(event: Event) {
  void upload(Array.from((event.target as HTMLInputElement).files || []))
}
function onDrop(event: DragEvent) {
  dragging.value = false
  void upload(Array.from(event.dataTransfer?.files || []))
}
let dingAudio: HTMLAudioElement | null = null
function playDing() {
  try {
    dingAudio ??= new Audio('/prompt.mp3')
    dingAudio.currentTime = 0
    dingAudio.play().catch(() => { /* 用户未交互时自动播放会被浏览器拦截，静默忽略 */ })
  } catch {}
}
function useSuggestion(text: string) {
  if (!user.value) return openModal('login')
  query.value = text
  composer.value?.focus()
}
async function scrollChat() {
  await nextTick()
  messagesEnd.value?.scrollIntoView({ behavior: 'smooth', block: 'end' })
}
async function ask() {
  if (!user.value) return openModal('login')
  if (!query.value.trim() || asking.value || historyLoading.value) return
  // projectId 为 null = 默认窗口（不创项目，只走工具/直接问答，不检索资料）。
  const projectId = activeId.value
  // 无文档也可提问:检索无上下文即走空;工具/点名由后端判。
  if (
    projectId !== null &&
    excerptDocument.value &&
    selectedIds.value.length &&
    !selectedIds.value.includes(excerptDocument.value)
  ) {
    error.value = '原文所在文档需要包含在已选资料中。'
    return
  }
  const id = projectId ?? DEFAULT_KEY
  const version = sessionVersion
  const chat = (chats.value[id] ??= { turns: [] })
  const text = query.value.trim()
  // 气泡只显示干净的问题；划词引用的 AI 原话仅作为前缀发给后端，不进气泡。
  const sendText = quoted.value
    ? `关于你上一条回答里的这段：「${quoted.value}」\n\n${text}`
    : text
  const turn: Turn = { question: text }
  const turnIndex = chat.turns.push(turn) - 1
  const body = {
    query: sendText,
    conversation_id: chat.conversationId ?? null,
    candidate_k_percentage: recall.value / 100,
    ...(agentImage.value ? { timetable_image_id: agentImage.value.id } : {}),
    // 文档范围/划词只在项目窗口有意义；默认窗口不带这些字段。
    ...(projectId !== null && selectedIds.value.length ? { doc_ids: [...selectedIds.value] } : {}),
    ...(projectId !== null && excerptDocument.value
      ? { document_id: excerptDocument.value, selected_text: excerpt.value.trim() }
      : {}),
  }
  query.value = ''
  quoted.value = null
  clearExcerpt() // 划词原文也做成一次性：发送即清，原文追问不再关不掉
  // 默认窗口不支持后台任务（durable），强制走流式。
  await runAsk(projectId, chat, turnIndex, body, version, durableAsk.value && projectId !== null)
}
/** 实际发起一轮问答:流式或后台任务,统一处理中断、错误与收尾。ask() 和确认执行共用。 */
async function runAsk(
  projectId: number | null,
  chat: Chat,
  turnIndex: number,
  body: Record<string, unknown>,
  version: number,
  durable: boolean,
) {
  asking.value = true
  error.value = ''
  const seq = ++askSeq
  askingTurn = { chat, index: turnIndex }
  askInterrupted = false
  askController = new AbortController()
  const signal = askController.signal
  void scrollChat()
  try {
    if (durable && projectId !== null) {
      const result = await post<Job>(`/projects/${projectId}/ask/jobs`, body, signal)
      if (version !== sessionVersion) return
      chat.turns[turnIndex]!.jobId = result.job_id
      await pollAnswer(projectId, chat, turnIndex, result.job_id, version)
    } else {
      await streamAsk(
        projectId === null ? '/ask/stream' : `/projects/${projectId}/ask/stream`,
        body,
        {
          onToken: (text) => {
            if (version !== sessionVersion || seq !== askSeq || askInterrupted) return
            const turn = chat.turns[turnIndex]
            if (!turn) return
            if (!turn.result) {
              turn.result = {
                answer: '',
                sources: [],
                retrieved_contexts: [],
                conversation_id: chat.conversationId ?? 0,
              }
            }
            turn.result.answer += text
          },
          onDone: (answer) => {
            if (version !== sessionVersion || seq !== askSeq || askInterrupted) return
            const turn = chat.turns[turnIndex]
            if (!turn) return
            turn.result = answer
            chat.conversationId = answer.conversation_id
          },
        },
        signal,
      )
    }
    if (version === sessionVersion && chat.turns[turnIndex]?.result?.tool_results?.some(
      item => item.tool === 'supplement_timetable_image',
    )) agentImage.value = null
  } catch (e) {
    if (version !== sessionVersion || seq !== askSeq) return
    if (askInterrupted || (e instanceof DOMException && e.name === 'AbortError')) {
      chat.turns[turnIndex]!.stopped = true
      return
    }
    chat.turns[turnIndex]!.error = e instanceof Error ? e.message : '回答失败'
    if (e instanceof ApiError && e.status === 401) report(e)
  } finally {
    if (seq === askSeq) {
      askController = null
      askingTurn = null
    }
    if (seq === askSeq && version === sessionVersion) asking.value = false
    void scrollChat()
  }
}
/** 用户点回答里的「确认执行」:新开一轮,把待执行操作原样回传后端直接执行。 */
async function confirmAction(pending: PendingAction) {
  if (!user.value || asking.value || historyLoading.value) return
  const projectId = activeId.value
  const id = projectId ?? DEFAULT_KEY
  const version = sessionVersion
  const chat = (chats.value[id] ??= { turns: [] })
  const turn: Turn = { question: '确认执行上述操作' }
  const turnIndex = chat.turns.push(turn) - 1
  const body = {
    query: '确认执行上述操作',
    conversation_id: chat.conversationId ?? null,
    candidate_k_percentage: recall.value / 100,
    confirm_action: { name: pending.name, args: pending.args },
  }
  void scrollChat()
  await runAsk(projectId, chat, turnIndex, body, version, false)
}
/** 停止当前这轮问答：中断请求、结束轮询，这一轮标记为“已停止” */
function stopAsking() {
  if (!asking.value) return
  askInterrupted = true
  if (askingTurn) askingTurn.chat.turns[askingTurn.index]!.stopped = true
  askController?.abort()
  askController = null
  wakeAsk?.()
  asking.value = false
}
async function pollAnswer(id: number, chat: Chat, index: number, jobId: string, version: number) {
  for (let attempt = 0; attempt < 180; attempt++) {
    if (askInterrupted) {
      chat.turns[index]!.stopped = true
      return
    }
    const job = await request<Job>(`/projects/${id}/jobs/${jobId}`)
    if (version !== sessionVersion || askInterrupted) {
      if (askInterrupted) chat.turns[index]!.stopped = true
      return
    }
    if (job.status === 'completed' && job.result) {
      chat.turns[index]!.result = job.result
      chat.conversationId = job.result.conversation_id
      playDing()
      if (activeId.value === id) void loadDocuments(id)
      return
    }
    if (job.status === 'failed') throw new Error('任务失败，可在“历史与任务”中重试。')
    await new Promise<void>((resolve) => {
      wakeAsk = resolve
      askTimer = setTimeout(resolve, 2000)
    })
    wakeAsk = undefined
    if (version !== sessionVersion) return
  }
  throw new Error('任务仍在后台处理，可在“历史与任务”中继续查看。')
}
async function openConversation(conversationId: number) {
  if (asking.value) return
  const projectId = activeId.value,
    id = projectId ?? DEFAULT_KEY,
    base = projectId === null ? '' : `/projects/${projectId}`,
    version = sessionVersion,
    sequence = ++historyRequest
  historyLoading.value = true
  try {
    const messages: HistoryMessage[] = []
    let offset = 0,
      total = 1
    while (offset < total) {
      const result = await request<PageResult<HistoryMessage>>(
        `${base}/conversations/${conversationId}/messages?limit=100&offset=${offset}`,
      )
      if (version !== sessionVersion || projectId !== activeId.value || sequence !== historyRequest) return
      messages.push(...result.items)
      total = result.total
      offset += result.items.length
      if (!result.items.length) break
    }
    const turns: Turn[] = [],
      paired = new Map<string | number, Turn>()
    for (const message of messages) {
      if (message.role === 'user') {
        const turn = { question: message.content }
        turns.push(turn)
        paired.set(message.turn_id, turn)
      } else if (message.role === 'assistant') {
        let turn = paired.get(message.turn_id)
        if (!turn) {
          turn = { question: '历史回答' }
          turns.push(turn)
        }
        turn.result = {
          answer: message.content,
          conversation_id: conversationId,
          sources: message.response_data?.sources || [],
          retrieved_contexts: message.response_data?.retrieved_contexts || [],
          tool_results: message.response_data?.tool_results,
        }
      }
    }
    for (const turn of turns) if (!turn.result) turn.error = '此条历史消息暂无已保存回答。'
    chats.value[id] = { turns, conversationId }
    source.value = null
    activityOpen.value = false
    void scrollChat()
  } catch (e) {
    if (version === sessionVersion && sequence === historyRequest) report(e)
  } finally {
    if (sequence === historyRequest) historyLoading.value = false
  }
}
function imported(projectId: number) {
  if (projectId === activeId.value) void loadDocuments(projectId)
}
async function loadLatestConversation(projectId: number | null) {
  const version = sessionVersion
  const base = projectId === null ? '' : `/projects/${projectId}`
  try {
    const result = await request<PageResult<{ id: number }>>(
      `${base}/conversations?limit=1&offset=0`,
    )
    if (version !== sessionVersion || projectId !== activeId.value) return
    if (result.items.length) await openConversation(result.items[0].id)
  } catch {
    // 后台预载失败时保持欢迎页，不打断进入项目
  }
}
/** 历史会话被删掉：删的正是当前打开的那段时，回到空白新对话 */
function conversationDeleted(conversationId: number) {
  const id = activeId.value ?? DEFAULT_KEY
  const chat = chats.value[id]
  if (!chat || chat.conversationId !== conversationId) return
  chats.value[id] = { turns: [] }
  source.value = null
  query.value = ''
}
function newChat() {
  if (!asking.value && !historyLoading.value) {
    chats.value[activeId.value ?? DEFAULT_KEY] = { turns: [] }
    source.value = null
    query.value = ''
  }
}
function downloadChat() {
  if (!currentChat.value) return
  const text = currentChat.value.turns
    .map(
      (t) =>
        `## ${t.question}\n\n${t.result?.answer || t.error || ''}\n\n${t.result?.sources.map((s) => `[${s.index}] ${s.filename}`).join('\n') || ''}`,
    )
    .join('\n\n---\n\n')
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = '学术问答.md'
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
function selectImage(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  prepareTimetableImage(file)
}
function prepareTimetableImage(file: File) {
  if (recognizing.value) return
  if (!['image/png', 'image/jpeg'].includes(file.type) || !file.size) {
    error.value = '请选择非空的 PNG 或 JPEG 图片。'
    return
  }
  if (timetablePreview.value) URL.revokeObjectURL(timetablePreview.value)
  timetableFile.value = file
  timetablePreview.value = URL.createObjectURL(file)
  courses.value = []
}
async function recognizeTimetable() {
  if (!user.value) return openModal('login')
  if (!timetableFile.value || recognizing.value) return
  recognizing.value = true
  error.value = ''
  courses.value = []
  try {
    const form = new FormData()
    form.append('file', timetableFile.value)
    const result = await request<{ message: string; timetables: Course[] } | string>('/timetable', {
      method: 'POST',
      body: form,
    })
    if (typeof result === 'string') {
      error.value = `${result}。部分课程可能已保存，请检查原图后重试。`
      return
    }
    courses.value = result.timetables
    notice.value = result.message + '，课程已保存。'
    void savedTimetable.value?.reload()
  } catch (e) {
    report(e)
  } finally {
    recognizing.value = false
  }
}
onMounted(bootstrap)
onUnmounted(() => {
  sessionVersion++
  clearTimeout(askTimer)
  wakeAsk?.()
  clearTimeout(pollTimer)
  docRequest++
  if (timetablePreview.value) URL.revokeObjectURL(timetablePreview.value)
})
</script>
 
<template>
  <!-- 登录页：未登录时显示 -->
  <div v-if="!user" class="auth-landing">
    <div class="auth-bg"></div>
    <div class="auth-overlay"></div>
    <div class="auth-content">
      <img
        class="auth-brand-icon"
        src="/brand-icon.png"
        alt="BNBU Academic Agent"
      />
      <h1 class="auth-title">BNBU Academic Agent</h1>
      <div class="auth-actions">
        <button class="auth-btn auth-btn-login" @click="openModal('login')">
          登录 / Login
        </button>
        <button class="auth-btn auth-btn-register" @click="openModal('register')">
          注册 / Register
        </button>
      </div>
    </div>
  </div>
 
  <!-- 主应用：登录后显示 -->
  <div v-else class="app-shell">
    <!-- 收起状态下的左栏展开标签（贴屏幕最左边缘） -->
    <button
      v-if="sidebarCollapsed"
      class="collapsed-tab collapsed-tab-left"
      aria-label="展开左栏"
      @click="sidebarCollapsed = false"
    >
      <PanelLeftOpen :size="14" />
    </button>
    <button
      v-if="sidebarOpen"
      class="sidebar-scrim"
      aria-label="关闭导航"
      @click="sidebarOpen = false"
    ></button>
    <aside
      class="sidebar"
      :class="{ 'is-open': sidebarOpen, 'sidebar-collapsed': sidebarCollapsed }"
      :style="{ borderRightWidth: sidebarCollapsed ? '0px' : '1px' }"
    >
      <div class="brand-row">
        <a class="brand" href="#" @click.prevent="view = 'research'; activeId = null"
          ><img class="brand-mark-img" src="/brand-icon.png" alt="BNBU" />
          <span>BNBU <span class="brand-agent">Agent</span></span></a
        >
        <button
          class="top-toggle top-toggle-left"
          :aria-label="sidebarCollapsed ? '展开左栏' : '收起左栏'"
          @click="sidebarCollapsed = !sidebarCollapsed"
        >
          <PanelLeftClose v-if="!sidebarCollapsed" :size="14" />
          <PanelLeftOpen v-else :size="14" />
        </button>
      </div>
      <button class="create-button" @click="newProject">
        <Plus :size="17" /> 新建研究项目 <span>＋</span>
      </button>
      <nav class="primary-nav" aria-label="工作台导航">
        <button :class="{ active: view === 'research' }" @click="changeView('research')">
          <BookOpen :size="18" />研究工作台 <span class="nav-dot" v-if="view === 'research'"></span>
        </button>
        <button :class="{ active: view === 'timetable' }" @click="changeView('timetable')">
          <CalendarDays :size="18" />我的课表
        </button>
        <button :class="{ active: view === 'school' }" @click="changeView('school')">
          <GraduationCap :size="18" />学校事务
        </button>
      </nav>
      <div class="project-item-wrap default-chat-wrap" :class="{ selected: view === 'research' && activeId === null }">
        <button
          class="project-item"
          :class="{ selected: view === 'research' && activeId === null }"
          @click="openDefaultChat"
        >
          <Sparkles :size="16" /><span>默认对话</span><ChevronRight :size="13" />
        </button>
      </div>
      <div class="section-caption">
        我的项目 <span>{{ projects.length.toString().padStart(2, '0') }}</span>
      </div>
      <label class="project-search"
        ><Search :size="15" /><input v-model="filter" placeholder="搜索项目" aria-label="搜索项目"
      /></label>
      <div class="project-list">
        <p v-if="booting || projectLoading" class="sidebar-hint">正在加载项目…</p>
        <div
          v-for="project in visibleProjects"
          :key="project.id"
          class="project-item-wrap"
          :class="{ selected: activeId === project.id }"
        >
          <button
            class="project-item"
            :class="{ selected: activeId === project.id }"
            @click="chooseProject(project.id)"
          >
            <FolderOpen :size="16" /><span>{{ project.name }}</span
            ><ChevronRight :size="13" />
          </button>
          <button
            class="project-delete"
            :aria-label="`删除项目：${project.name}`"
            title="删除项目"
            @click.stop="askDeleteProject(project)"
          >
            <Trash2 :size="14" />
          </button>
        </div>
        <p v-if="!booting && !projectLoading && !visibleProjects.length" class="sidebar-hint">
          {{ filter ? '没有匹配的项目' : '每一个研究，从一个项目开始。' }}
        </p>
      </div>
      <div class="sidebar-bottom">
        <button class="help-button" @click="openModal('help')">
          <CircleHelp :size="17" />使用指南 <ArrowRight :size="14" />
        </button>
        <div class="account-row">
          <span class="avatar">{{ user?.username.slice(0, 1) || '访' }}</span>
          <div>
            <strong>{{ user?.username || '访客' }}</strong
            ><small>{{ user ? '个人账号' : '登录后开启研究' }}</small>
          </div>
          <button
            v-if="user"
            class="icon-button"
            :disabled="anyBusy"
            aria-label="退出登录"
            title="退出登录"
            @click="logout"
          >
            <LogOut :size="17" /></button
          ><button v-else class="text-button" @click="openModal('login')">登录</button>
        </div>
      </div>
    </aside>
    <!-- 左栏 resizer 放在 sidebar 外面（sidebar 有 overflow:hidden，absolute 子元素会被裁） -->
    <button
      v-if="!sidebarCollapsed"
      class="pane-resizer pane-resizer-left"
      aria-label="拖拽调整左栏宽度"
      @mousedown="startDrag('left', $event)"
    ></button>
 
    <div class="main-shell">
      <header class="topbar">
        <div class="breadcrumbs">
          <button class="icon-button mobile-menu" aria-label="打开导航" @click="sidebarOpen = true">
            <Menu :size="20" /></button
          ><span>工作空间</span><ChevronRight :size="13" /><strong>{{
            view === 'timetable'
              ? '我的课表'
              : view === 'school'
                ? '学校事务'
                : activeProject?.name || '研究工作台'
          }}</strong>
        </div>
        <div class="topbar-right">
        </div>
      </header>
      <div v-if="error" class="toast error" role="alert">
        <span>{{ error }}</span
        ><button class="icon-button" aria-label="关闭错误提示" @click="error = ''">
          <X :size="16" />
        </button>
      </div>
      <div v-if="notice" class="toast success" role="status">
        <Check :size="18" /><span>{{ notice }}</span
        ><button class="icon-button" aria-label="关闭提示" @click="notice = ''">
          <X :size="16" />
        </button>
      </div>
 
      <div v-if="view === 'research'" class="research-layout">
        <main class="research-main">
          <div v-if="!activeId" class="welcome-full-bg"></div>
          <div class="research-toolbar" :class="{ 'toolbar-slim': !activeId }">
            <div>
              <h1>{{ activeProject?.name || '研究工作台' }}</h1>
              <span class="tiny-label">RESEARCH SPACE</span>
            </div>
            <div v-if="activeId || currentChat?.turns.length" class="toolbar-actions">
              <button
                v-if="activeId"
                class="subtle-button"
                :disabled="!activeId || historyLoading"
                @click="activityOpen = !activityOpen"
              >
                历史与任务
              </button>
              <button
                class="icon-button"
                :disabled="!currentChat?.turns.length"
                title="导出对话"
                aria-label="导出对话"
                @click="downloadChat"
              >
                <ArrowDownToLine :size="18" /></button
              ><button
                class="subtle-button"
                :disabled="!currentChat?.turns.length || asking || historyLoading"
                @click="newChat"
              >
                <Plus :size="16" />新对话</button
              ><button
                v-if="activeId"
                class="icon-button docs-toggle"
                aria-label="查看项目资料"
                @click="mobileDocs = !mobileDocs"
              >
                <FileText :size="18" />
              </button>
            </div>
          </div>
 
          <nav
            v-if="currentChat && currentChat.turns.length > 1"
            class="turn-dots"
            aria-label="对话导航"
          >
            <button
              v-if="dotPageCount > 1"
              type="button"
              class="turn-page-btn"
              :disabled="dotPage === 0"
              aria-label="上一组对话"
              title="上一组"
              @click="shiftDotPage(-1)"
            >
              <ChevronUp :size="12" />
            </button>
            <span
              v-for="item in dotTurns"
              :key="item.index"
              class="turn-dot-wrap"
              @click="scrollToTurn(item.index)"
            >
              <i class="turn-dot" :class="{ active: item.index === activeTurn }"></i>
              <span class="turn-dot-tip" role="tooltip">{{ turnPreview(item.turn.question) }}</span>
            </span>
            <button
              v-if="dotPageCount > 1"
              type="button"
              class="turn-page-btn"
              :disabled="dotPage >= dotPageCount - 1"
              aria-label="下一组对话"
              title="下一组"
              @click="shiftDotPage(1)"
            >
              <ChevronDown :size="12" />
            </button>
            <span v-if="dotPageCount > 1" class="turn-page-indicator"
              >{{ dotPage + 1 }}/{{ dotPageCount }}</span
            >
          </nav>
          <div class="chat-scroll" @scroll.passive="onChatScroll">
            <ProjectActivity
              v-if="activityOpen && activeId && user"
              :key="`${user.id}:${activeId}`"
              :project-id="activeId"
              :locked="asking || historyLoading"
              @error="report"
              @conversation="openConversation"
              @deleted="conversationDeleted"
              @refresh="loadDocuments(activeId)"
            />
            <p v-if="historyLoading" class="feature-notice" role="status">正在恢复历史会话…</p>
            <section v-if="!activeId && !currentChat?.turns.length" class="welcome welcome-default">
              <div class="welcome-paper-content">
                <img class="welcome-emblem-img" src="/brand-icon.png" alt="BNBU" />
                <div class="welcome-title">Academic Agent</div>
                <div class="button-row">
                  <button class="primary-button welcome-new-project" @click="newProject()">
                    <Plus :size="20" />新建研究项目<ArrowRight :size="16" />
                  </button>
                  <button class="primary-button welcome-open-existing" @click="openProjectPicker = !openProjectPicker">
                    <FolderOpen :size="18" />打开已有项目<ArrowRight :size="16" />
                  </button>
                </div>
                <div v-if="openProjectPicker" class="project-picker">
                  <div v-if="projects.length === 0" class="picker-empty">暂无项目，先创建一个吧</div>
                  <button
                    v-for="p in projects"
                    :key="p.id"
                    class="picker-item"
                    @click="activeId = p.id; openProjectPicker = false"
                  >
                    <Folder :size="16" />
                    <span>{{ p.name }}</span>
                  </button>
                </div>
                <form class="welcome-composer" @submit.prevent="ask">
                  <textarea
                    ref="composer"
                    v-model="query"
                    :disabled="!user"
                    maxlength="10000"
                    rows="2"
                    placeholder="直接提问，或查询作业 / 课表 / 提醒（默认助手不检索资料）…"
                    aria-label="输入问题"
                    @keydown.enter="
                      (event) => {
                        if (!event.shiftKey && !event.isComposing) {
                          event.preventDefault()
                          ask()
                        }
                      }
                    "
                  ></textarea>
                  <div class="welcome-composer-bottom">
                    <span class="welcome-scope-hint"
                      ><Layers3 :size="13" />默认助手 · 工具与直接问答，不检索资料</span
                    >
                    <button
                      v-if="asking"
                      type="button"
                      class="send-button stop"
                      aria-label="停止回答"
                      title="停止回答"
                      @click="stopAsking"
                    >
                      <Square :size="13" fill="currentColor" />
                    </button>
                    <button
                      v-else
                      type="submit"
                      class="send-button"
                      :disabled="!query.trim()"
                      aria-label="发送问题"
                    >
                      <ArrowUp :size="16" />
                    </button>
                  </div>
                </form>
                <div class="welcome-toolbar">
                  <div class="welcome-toggles">
                    <label class="check-label"
                      ><input v-model="durableAsk" type="checkbox" :disabled="asking" />后台问答
                      <span
                        class="hint-wrap"
                        tabindex="0"
                        aria-label="后台问答说明"
                        @click.prevent
                        ><CircleHelp :size="13" /><span class="hint-pop" role="tooltip"
                          >勾选后，提问会作为后台任务执行：可以切换页面或刷新，任务继续运行，稍后从「历史与任务」找回结果。适合耗时较长的问题。</span
                        ></span
                      ></label
                    >
                  </div>
                  <small class="welcome-toolbar-note"
                    >仅按本轮明确要求执行。课表操作包含学校获取、图片补充、增删改；学校密码请在弹出的学校窗口输入。</small
                  >
                </div>
              </div>
            </section>
            <section v-else-if="activeId && !currentChat?.turns.length" class="welcome welcome-empty-chat">
              <img class="welcome-emblem-img" src="/brand-icon.png" alt="BNBU" />
              <div class="welcome-title">{{ activeProject?.name }}</div>
              <div class="empty-chat-suggestions">
                <button
                  class="suggestion-chip"
                  @click="useSuggestion('请帮我总结这些文献的研究问题、研究方法和核心结论，并标注来源。')"
                >
                  <FileText :size="18" />
                  <span>梳理文献脉络</span>
                </button>
                <button
                  class="suggestion-chip"
                  @click="useSuggestion('请提取这些资料中的关键概念，用通俗的语言解释，并给出原文依据。')"
                >
                  <Sparkles :size="18" />
                  <span>概念通俗解读</span>
                </button>
                <button
                  class="suggestion-chip"
                  @click="useSuggestion('请帮我查找与研究主题相关的资料线索，并说明资料来源。')"
                >
                  <Search :size="18" />
                  <span>检索相关资料</span>
                </button>
              </div>
            </section>
            <section v-else class="messages" aria-label="研究对话" aria-live="polite">
              <article
                v-for="(turn, index) in currentChat.turns"
                :id="`turn-${index}`"
                :key="index"
                class="turn"
              >
                <div class="question">
                  <span class="message-avatar">{{ user?.username.slice(0, 1) }}</span>
                  <div>
                    <span class="message-label">你</span>
                    <p>{{ turn.question }}</p>
                  </div>
                </div>
                <div class="answer">
                  <span class="message-avatar ai"><Sparkles :size="17" /></span>
                  <div class="answer-content">
                    <span class="message-label"
                      >Academic Agent <span class="ai-label">研究助手</span></span
                    >
                    <div
                      v-if="turn.result"
                      class="markdown"
                      v-html="answerHtml(turn.result.answer)"
                      @click="onAnswerClick($event, turn)"
                      @mouseup="onAnswerSelect"
                    ></div>
                    <p v-else-if="turn.error" class="turn-error">
                      {{ turn.error }}
                      <button class="text-button" @click="editQuestion(turn.question)">
                        重新编辑
                      </button>
                    </p>
                    <p v-else-if="turn.stopped" class="turn-stopped">已停止这一轮回答</p>
                    <p v-else class="thinking">
                      <LoaderCircle :size="16" class="spin" />{{ activeId ? '正在检索资料、整理回答…' : '正在整理回答…' }}
                      <button type="button" class="text-button" @click="stopAsking">停止</button>
                    </p>
                    <div
                      v-if="
                        turn.result?.pending_action &&
                        index === currentChat.turns.length - 1 &&
                        !asking
                      "
                      class="confirm-action"
                    >
                      <button
                        type="button"
                        class="confirm-btn"
                        @click="confirmAction(turn.result.pending_action!)"
                      >
                        <Check :size="14" />确认执行
                      </button>
                      <button
                        type="button"
                        class="text-button"
                        @click="turn.result.pending_action = null"
                      >
                        取消
                      </button>
                    </div>
                    <p v-if="turn.jobId" class="job-id">
                      任务 {{ turn.jobId }} · 刷新后可从“历史与任务”找回
                    </p>
                    <div v-if="turn.result?.sources.length" class="source-chips">
                      <button
                        v-for="group in citationGroups(turn)"
                        :key="group.items[0].citation.index"
                        class="source-chip"
                        :title="`在预览中查看「${group.filename}」的引用位置（写入批注在预览窗口里）`"
                        @click="openCitationGroup(turn, group)"
                      >
                        <span>{{ group.items[0].citation.index }}</span
                        ><FileText :size="12" />{{ group.filename
                        }}<em v-if="group.items.length > 1">×{{ group.items.length }}</em>
                      </button>
                    </div>
                  </div>
                </div>
              </article>
              <div ref="messagesEnd"></div>
            </section>
            <!-- AI 回答划词后的浮动「追问」按钮：mousedown 阻断，避免清掉选区/被全局监听收起 -->
            <button
              v-if="quoteBtn"
              type="button"
              class="quote-pop"
              :style="{ left: quoteBtn.x + 'px', top: quoteBtn.y + 'px' }"
              @mousedown.stop.prevent="pickQuote"
            >
              追问这段 <ArrowRight :size="13" />
            </button>
          </div>
 
          <div v-if="activeId || currentChat?.turns.length" class="composer-wrap">
            <div v-if="settingsOpen" class="ask-settings">
              <label
                >检索范围比例 <strong>{{ recall }}%</strong
                ><input v-model.number="recall" type="range" min="5" max="100" step="5" /></label>
            </div>
            <div class="school-tool-settings">
              <label class="check-label"
                ><input
                  v-model="durableAsk"
                  type="checkbox"
                  :disabled="asking"
                />后台问答（支持恢复）<span
                  class="hint-wrap"
                  tabindex="0"
                  aria-label="后台问答说明"
                  @click.prevent
                  ><CircleHelp :size="13" /><span class="hint-pop" role="tooltip"
                    >勾选后，提问会作为后台任务执行：可以切换页面或刷新，任务继续运行，稍后从「历史与任务」找回结果。适合耗时较长的问题。</span
                  ></span
                ></label
              >
              <small
                >仅按本轮明确要求执行。课表操作包含学校获取、图片补充、增删改；学校密码请在弹出的学校窗口输入。选定文档或附带原文时优先文档问答。</small
              >
            </div>
            <form class="composer" @submit.prevent="ask">
              <div class="field-row">
                <input ref="agentImageInput" class="visually-hidden" type="file" accept="image/png,image/jpeg" aria-label="附加课表图片" @change="attachAgentImage" />
                <button type="button" class="text-button" :disabled="asking || agentImageUploading || !!selectedIds.length || !!excerptDocument" @click="agentImageInput?.click()">{{ agentImageUploading ? '正在上传图片…' : '附加课表图片' }}</button>
                <span v-if="agentImage">{{ agentImage.name }} · 本轮课表附件</span>
                <button v-if="agentImage" type="button" class="text-button" :disabled="asking || agentImageUploading" @click="agentImage = null">移除图片附件</button>
              </div>
              <div v-if="quoted" class="quote-chip">
                <ArrowRight :size="12" />
                <span class="quote-chip-text">{{ quoted }}</span>
                <button type="button" class="quote-chip-x" aria-label="取消引用" @click="quoted = null">
                  <X :size="13" />
                </button>
              </div>
              <div v-else-if="excerptDocument" class="quote-chip">
                <ArrowRight :size="12" />
                <span class="quote-chip-doc" :title="excerptDocName">{{ excerptDocName }}</span>
                <span class="quote-chip-text">{{ excerpt }}</span>
                <button type="button" class="quote-chip-x" aria-label="取消引用" @click="clearExcerpt">
                  <X :size="13" />
                </button>
              </div>
              <textarea
                ref="composer"
                v-model="query"
                :disabled="!user"
                maxlength="10000"
                rows="2"
                :placeholder="
                  !user
                    ? '登录后，开启与你的资料的第一场对话…'
                    : !activeId
                      ? '直接提问，或查询作业 / 课表 / 提醒（默认助手不检索资料）…'
                      : !selectedIds.length && !excerptDocument
                        ? '查询学校作业、课表、提醒，或明确要求执行操作…'
                        : !readyDocs.length
                          ? '先上传资料，待解析完成后即可提问…'
                          : '有什么想从资料中了解的？'
                "
                aria-label="输入研究问题"
                @keydown.enter="
                  (event) => {
                    if (!event.shiftKey && !event.isComposing) {
                      event.preventDefault()
                      ask()
                    }
                  }
                "
              ></textarea>
              <div class="composer-bottom">
                <div v-if="activeId" class="scope-anchor">
                  <div v-if="scopeOpen" class="scope-backdrop" @click="scopeOpen = false"></div>
                  <button
                    type="button"
                    class="scope-button"
                    :aria-expanded="scopeOpen"
                    title="选择提问时检索哪些资料"
                    @click="scopeOpen = !scopeOpen"
                  >
                    <Layers3 :size="14" />{{
                      selectedIds.length ? `已选 ${selectedIds.length} 份资料` : '全部项目资料'
                    }}<span v-if="excerptDocument" class="excerpt-label">附带原文</span>
                  </button>
                  <div v-if="scopeOpen" class="scope-pop">
                    <div class="scope-pop-head">
                      <strong>选择检索资料</strong>
                      <button
                        v-if="selectedIds.length"
                        type="button"
                        class="scope-clear"
                        @click="selectedIds = []"
                      >
                        使用全部资料
                      </button>
                    </div>
                    <p v-if="loadingDocs" class="scope-empty">正在读取资料…</p>
                    <p v-else-if="!documents.length" class="scope-empty">
                      项目还没有已导入的资料，先在右侧栏上传
                    </p>
                    <div v-else class="scope-list">
                      <label v-for="doc in documents" :key="doc.id" class="scope-item">
                        <input
                          v-model="selectedIds"
                          type="checkbox"
                          :value="doc.id"
                          :disabled="doc.status !== 'completed'"
                        />
                        <span class="scope-name" :title="doc.filename">{{ doc.filename }}</span>
                        <small :class="doc.status">{{ statusText(doc.status) }}</small>
                      </label>
                    </div>
                    <p v-if="documents.length" class="scope-pop-hint">
                      {{ selectedIds.length ? '只检索勾选的资料' : '未勾选时检索全部资料' }}
                    </p>
                  </div>
                </div>
                <div>
                  <button
                    type="button"
                    class="icon-button"
                    :disabled="!activeId"
                    :aria-expanded="settingsOpen"
                    aria-label="问答设置"
                    @click="settingsOpen = !settingsOpen"
                  >
                    <Settings2 :size="17" /></button
                  ><button
                    v-if="asking"
                    type="button"
                    class="send-button stop"
                    aria-label="停止回答"
                    title="停止回答"
                    @click="stopAsking"
                  >
                    <Square :size="14" fill="currentColor" /></button
                  ><button
                    v-else
                    type="submit"
                    class="send-button"
                    :disabled="!canAsk"
                    aria-label="发送问题"
                  >
                    <LoaderCircle v-if="asking" :size="19" class="spin" /><ArrowUp
                      v-else
                      :size="19"
                    />
                  </button>
                </div>
              </div>
            </form>
            <p class="composer-hint">
              <template v-if="asking">
                <button type="button" class="text-button" @click="stopAsking">
                  停止回答
                </button>
                <span>可随时中断当前这一轮回答</span>
              </template>
              <template v-else>
                回答由 AI 生成，请结合引用原文核实。<span>Enter 发送 · Shift + Enter 换行</span>
              </template>
            </p>
          </div>
        </main>
 
        <!-- 右栏 resizer（展开时显示） -->
        <button
          v-if="!documentsCollapsed"
          class="pane-resizer pane-resizer-right"
          aria-label="拖拽调整右栏宽度"
          @mousedown="startDrag('right', $event)"
        ></button>
 
        <!-- 右栏收起时的展开图标：和 resizer-right 在 DOM 里紧挨，位置相同 -->
        <button
          v-if="documentsCollapsed"
          class="collapsed-tab collapsed-tab-right"
          aria-label="展开右栏"
          @click="documentsCollapsed = false"
        >
          <PanelRightOpen :size="14" />
        </button>
 
        <aside
          class="documents-panel"
          :class="{
            'mobile-visible': mobileDocs,
            'documents-collapsed': documentsCollapsed,
            'viewer-mode': !!viewerDoc,
          }"
          :style="{ borderLeftWidth: documentsCollapsed ? '0px' : '1px' }"
        >
          <div class="panel-heading">
            <h2>
              <FileText :size="17" />项目资料 <span>{{ documents.length }}</span>
            </h2>
            <div class="panel-heading-actions">
              <button
                v-if="!documentsCollapsed"
                class="top-toggle top-toggle-right"
                :aria-label="documentsCollapsed ? '展开右栏' : '收起右栏'"
                @click="documentsCollapsed = !documentsCollapsed"
              >
                <PanelRightClose :size="14" />
              </button>
              <button
                v-if="documentsCollapsed"
                class="top-toggle top-toggle-right"
                aria-label="展开右栏"
                @click="documentsCollapsed = false"
              >
                <PanelRightOpen :size="14" />
              </button>
              <button
                class="icon-button panel-close"
                aria-label="关闭资料栏"
                @click="mobileDocs = false"
              >
                <X :size="18" />
              </button>
            </div>
          </div>
 
          <!-- Tab bar -->
          <div class="panel-tabs" v-if="viewerDoc">
            <button class="panel-tab" :class="{ active: !viewerDoc }" @click="viewerDoc = null">
              <FileText :size="13" /> 项目资料
            </button>
            <button class="panel-tab active viewer-tab">
              <BookOpen :size="13" />
              <span class="tab-filename" :title="viewerDoc.filename">{{ viewerDoc.filename }}</span>
              <span class="tab-close" aria-label="关闭文档" @click="viewerDoc = null">
                <X :size="12" />
              </span>
            </button>
          </div>
 
          <!-- Tab content: viewer -->
          <template v-if="viewerDoc">
            <DocumentViewer
              :project-id="activeId!"
              :document="viewerDoc"
              :highlights="viewerHighlights"
              :focus="viewerFocus"
              :annotating="annotating"
              :annotated-indices="viewerAnnotatedIndices"
              @close="viewerDoc = null"
              @error="report"
              @annotate="annotateInViewer"
              @download-annotated="downloadAnnotatedCopy"
              @quote="onViewerQuote"
            />
          </template>
 
          <!-- Tab content: list -->
          <template v-else>
            <template v-if="source"
              ><div class="source-detail">
                <button class="text-button" @click="source = null">← 返回资料列表</button>
                <h3>[{{ source.source.index }}] {{ source.source.filename }}</h3>
                <span class="tiny-label">引用原文</span>
                <p>{{ source.text }}</p>
                <button class="subtle-button" @click="askFromSource">
                  基于这段原文追问 <ArrowRight :size="14" />
                </button></div
            ></template>
            <template v-else>
              <p class="panel-description">为你的思考，添加可靠的依据。</p>
              <input
                ref="uploadInput"
                class="visually-hidden"
                type="file"
                accept=".pdf,.txt,.docx,.pptx"
                multiple
                aria-label="上传研究资料"
                @change="onFiles"
              />
              <button
                class="upload-zone"
                :class="{ dragging }"
                :disabled="!activeId || uploading"
                @click="uploadInput?.click()"
                @dragover.prevent="dragging = true"
                @dragleave.prevent="dragging = false"
                @drop.prevent="onDrop"
              >
                <span class="upload-icon"
                  ><LoaderCircle v-if="uploading" :size="21" class="spin" /><Upload
                    v-else
                    :size="21"
                    :stroke-width="1.5" /></span
                ><strong>{{ uploading ? '正在上传…' : activeId ? '点击或拖拽上传' : '请先创建并选择项目' }}</strong
                ><span>{{ activeId ? 'PDF、DOCX、PPTX、TXT' : '创建项目后即可在这里上传研究资料' }}</span>
              </button>
              <div class="document-list-heading">
                <span>资料列表</span
                ><button
                  class="text-button"
                  :disabled="!activeId || loadingDocs"
                  @click="activeId && loadDocuments(activeId)"
                >
                  刷新
                </button>
              </div>
              <p v-if="loadingDocs" class="empty-docs">
                <LoaderCircle :size="20" class="spin" />正在读取资料…
              </p>
              <div v-else-if="documents.length" class="document-list">
                <div v-for="doc in documents" :key="doc.id">
                  <label class="document-row"
                    ><input
                      v-model="selectedIds"
                      type="checkbox"
                      :value="doc.id"
                      :disabled="doc.status !== 'completed'"
                      :aria-label="`选择 ${doc.filename}`"
                    /><span class="file-icon" :class="doc.filename.split('.').pop()?.toLowerCase()"
                      ><FileText :size="19" /></span
                    ><span class="document-info"
                      ><strong :title="doc.filename">{{ doc.filename }}</strong
                      ><small :class="doc.status"
                        ><LoaderCircle
                          v-if="['queued', 'processing'].includes(doc.status)"
                          :size="10"
                          class="spin"
                        /><i v-else></i>{{ statusText(doc.status) }}</small
                      ></span
                    ></label
                  >
                  <DocumentActions
                    v-if="activeId"
                    :project-id="activeId"
                    :document="doc"
                    :locked="asking"
                    @error="report"
                    @notice="notice = $event"
                    @changed="loadDocuments(activeId)"
                    @view-doc="openDoc(doc)"
                  />
                </div>
                <button
                  v-if="selectedIds.length"
                  class="text-button clear-selection"
                  @click="selectedIds = []"
                >
                  清除选择 · 使用全部资料
                </button>
              </div>
              <div v-else class="empty-docs">
                <div class="empty-file-art"><FileText :size="33" :stroke-width="1" /></div>
                <strong>知识，从这里积累</strong
                ><span>{{
                  !activeId ? '创建项目后，上传你的第一份资料' : '上传第一份资料，让研究有据可循'
                }}</span>
              </div>
              <p v-if="processingCount" class="processing-note">
                {{ processingCount }} 份资料正在处理，状态将自动更新。
              </p>
              <div class="source-note">
                <span><BookOpen :size="16" />关于资料问答</span>
                <p>答案将附上引用来源，点击引用即可查看原文片段。</p>
                <p>勾选资料可限定检索范围；不勾选时使用整个项目。</p>
              </div>
            </template>
          </template>
        </aside>
      </div>
 
      <SchoolPanel
        v-else-if="view === 'school' && user"
        :key="user.id"
        :projects="projects"
        :project-id="activeId"
        @error="report"
        @imported="imported"
      />
      <main v-else-if="view === 'school'" class="feature-page">
        <h1>学校事务</h1>
        <p>登录后查看你的课程、作业和提醒。</p>
        <button class="primary-button" @click="openModal('login')">登录学校事务</button>
      </main>
      <main v-else class="timetable-main">
        <h1>学校课表 · MIS</h1>
        <MISTimetable v-if="user" :key="user.id" :recognizing="recognizing" @supplement="prepareTimetableImage" />
        <div class="timetable-upload">
          <input
            ref="imageInput"
            class="visually-hidden"
            type="file"
            accept="image/png,image/jpeg"
            aria-label="上传课表图片"
            @change="selectImage"
          /><button class="image-picker" :disabled="recognizing" @click="imageInput?.click()">
            <img v-if="timetablePreview" :src="timetablePreview" alt="待识别的课表预览" /><template
              v-else
              ><CalendarDays :size="35" :stroke-width="1.3" /><strong>选择课表图片</strong
              ><span>支持 PNG、JPEG</span></template
            >
          </button>
          <div class="timetable-instructions">
            <span class="step-number">01 — UPLOAD & ORGANIZE</span>
            <h2>用图片补充课表</h2>
            <p>学校课表有误或缺漏时，上传正确的 PNG/JPEG 图片。识别结果会补充到已保存课表，相同课程、时间和地点的记录会跳过。旧的错误安排请手动修改或删除。</p>
            <button
              class="primary-button"
              :disabled="recognizing || (!timetableFile && !!user)"
              @click="recognizeTimetable"
            >
              <LoaderCircle v-if="recognizing" class="spin" :size="16" /><Sparkles
                v-else
                :size="16"
              />{{
                recognizing ? '正在识别，请稍候…' : user ? '识别并保存课表' : '登录后识别'
              }}</button
            ><small v-if="timetableFile"
              >{{ timetableFile.name }}
              <button class="text-button" :disabled="recognizing" @click="imageInput?.click()">
                更换图片
              </button></small
            >
          </div>
        </div>
        <SavedTimetable v-if="user" :key="user.id" ref="savedTimetable" @error="report" @changed="courses = []" />
        <div v-if="courses.length" class="week-grid">
          <section v-for="day in days" :key="day[0]">
            <h3>{{ day[1] }}</h3>
            <article
              v-for="(course, i) in courses.filter((c) => c.day_of_week === day[0])"
              :key="i"
            >
              <span>{{ course.start_time }} – {{ course.end_time }}</span
              ><strong>{{ course.course_name }}</strong
              ><small>{{ course.location }}</small
              ><small v-if="course.teachers">{{ course.teachers }}</small>
            </article>
            <p v-if="!courses.some((c) => c.day_of_week === day[0])">暂无课程</p>
          </section>
        </div>
        <p class="timetable-note">
          识别结果请与原图核对。可在已保存课表中新增、修改或删除安排，并设置学期与单双周规则。
        </p>
      </main>
    </div>
 
 
  </div>
 
  <dialog ref="dialog" class="modal" @cancel.prevent="closeModal">
    <div class="modal-content">
      <button class="icon-button modal-close" :disabled="modalBusy" aria-label="关闭弹窗" @click="closeModal">
        <X :size="19" /></button
      >
      <template v-if="modal === 'login' || modal === 'register' || modal === 'reset'"
        ><h2>
          {{ modal === 'login' ? 'Welcome Back!' : modal === 'reset' ? 'Reset Password' : 'Get Started' }}
        </h2>
        <form @submit.prevent="authenticate" :class="{ 'register-form': modal === 'register' }">
          <label v-if="modal === 'register'">
            用户名
            <input v-model="username" required maxlength="20" autocomplete="username" placeholder="如何称呼你" />
          </label>
 
          <label>
            {{ modal === 'login' ? '邮箱或手机号' : '邮箱' }}
            <div v-if="modal === 'register'" class="email-field-wrap">
              <input
                type="email" v-model="account" required maxlength="255" autocomplete="username"
                placeholder="your@email.com"
                :class="{ 'email-valid': account && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(account) }"
              />
              <button
                type="button"
                class="send-code-btn"
                :disabled="codeBusy || modalBusy || !account || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(account)"
                @click="sendEmailCode"
              >{{ codeBusy ? '发送中…' : '发送验证码' }}</button>
            </div>
            <input
              v-else
              :type="modal === 'login' ? 'text' : 'email'" v-model="account" required maxlength="255" autocomplete="username"
              :placeholder="modal === 'login' ? '邮箱或手机号' : '邮箱地址'"
            />
          </label>
 
          <label v-if="modal === 'register'">
            邮箱验证码
            <input v-model="emailCode" required inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" placeholder="6 位验证码" />
            <p v-if="codeNotice" role="status" class="code-notice">{{ codeNotice }}</p>
          </label>
 
          <label>
            密码
            <input v-model="password" type="password" required :minlength="modal === 'login' ? 1 : 8" maxlength="128"
              :autocomplete="modal === 'login' ? 'current-password' : 'new-password'"
              :placeholder="modal === 'login' ? '输入密码' : '至少 8 位字符'"
            />
          </label>
 
          <label v-if="modal === 'register'">
            确认密码
            <input v-model="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="再次输入密码" />
          </label>
 
          <label v-if="modal === 'reset'">
            新密码
            <input v-model="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="至少 8 位" />
          </label>
 
          <label v-if="modal === 'register'" class="phone-label">
            手机号（选填）
            <input v-model="phone" type="tel" autocomplete="tel" maxlength="16" pattern="[+]?[0-9]{7,15}" placeholder="可留空，如 13800138000" />
          </label>
 
          <p v-if="modalError" class="form-error" role="alert">{{ modalError }}</p>
 
          <button class="primary-button full-width" :disabled="modalBusy">
            <LoaderCircle v-if="modalBusy" class="spin" :size="16" />
            {{ modalBusy ? '请稍候…' : modal === 'login' ? '登录' : modal === 'reset' ? '重置密码' : '创建账号' }}
            <ArrowRight v-if="!modalBusy" :size="16" />
          </button>
        </form>
        <button v-if="modal === 'login'" class="text-button" :disabled="modalBusy" @click="openModal('reset')">忘记密码？</button>
        <div class="auth-switch">
          {{ modal === 'login' ? '还没有账号？' : '已经有账号？' }}
          <button class="text-button" :disabled="modalBusy" @click="openModal(modal === 'login' ? 'register' : 'login')">{{ modal === 'login' ? '立即注册' : '去登录' }}</button>
        </div>
      </template>
      <template v-else-if="modal === 'project'"
        ><h2>一个项目，一次新的探索。</h2>
        <p>按课程、论文主题或研究方向整理你的资料。</p>
        <form @submit.prevent="createProject">
          <label>项目名称<input v-model="projectName" required maxlength="100" placeholder="例如：大语言模型文献研究" autofocus /></label>
          <p v-if="modalError" class="form-error" role="alert">{{ modalError }}</p>
          <button class="primary-button full-width" :disabled="modalBusy || !projectName.trim()"><LoaderCircle v-if="modalBusy" class="spin" :size="16" />创建项目<ArrowRight :size="16" /></button>
        </form>
      </template>
      <template v-else-if="modal === 'help'"
        ><h2>从资料到理解，只需三步。</h2>
        <ol class="help-steps"><li><strong>创建研究项目</strong><p>注册或登录后，为一个研究主题建立项目。</p></li><li><strong>上传你的资料</strong><p>支持 PDF、TXT、DOCX 和 PPTX。等待状态变为已就绪后即可提问。</p></li><li><strong>提问、追问与核实</strong><p>在同一对话内继续追问，点击引用查看证据。右侧勾选文件可缩小范围，问答设置中可以附带原文。</p></li></ol>
        <p>刷新后可从历史与任务打开已保存的会话并继续提问，也可导出 Markdown。后台问答任务可在任务列表中查看结果或重试。</p>
        <button class="primary-button full-width" @click="closeModal">开始探索 <ArrowRight :size="16" /></button>
      </template>
      <template v-else-if="modal === 'delete-project'"
        ><div class="modal-mark danger-mark"><Trash2 :size="20" /></div>
        <h2>删除这个项目？</h2>
        <p>
          项目「{{ projectToDelete?.name }}」中的<strong>全部文档、对话记录、检索结果与批注</strong>都会被永久删除，删除后无法恢复。
        </p>
        <p v-if="modalError" role="alert" class="danger-text">{{ modalError }}</p>
        <div class="button-row">
          <button
            class="subtle-button row-btn-danger"
            :disabled="modalBusy"
            @click="removeProject"
          >
            {{ modalBusy ? '正在删除…' : '确认删除项目' }}</button
          ><button class="text-button" :disabled="modalBusy" @click="closeModal">取消</button>
        </div>
      </template>
    </div>
  </dialog>
</template>