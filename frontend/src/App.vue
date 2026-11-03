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
  ChevronRight,
  CircleHelp,
  FileText,
  FolderOpen,
  GraduationCap,
  Layers3,
  LoaderCircle,
  LogOut,
  Menu,
  MessageSquare,
  Plus,
  Search,
  Settings2,
  Sparkles,
  Upload,
  X,
} from 'lucide-vue-next'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import SchoolPanel from './components/SchoolPanel.vue'
import SavedTimetable from './components/SavedTimetable.vue'
import ProjectActivity from './components/ProjectActivity.vue'
import DocumentActions from './components/DocumentActions.vue'
import MISTimetable from './components/MISTimetable.vue'
import {
  ApiError,
  post,
  request,
  type Answer,
  type Course,
  type Document,
  type Project,
  type Source,
  type User,
  type Job,
  type PageResult,
  type HistoryMessage,
} from './api'

interface Turn {
  question: string
  result?: Answer
  error?: string
  jobId?: string
}
interface Chat {
  turns: Turn[]
  conversationId?: number
}
const user = ref<User | null>(null)
const booting = ref(true)
const projects = ref<Project[]>([])
const activeId = ref<number | null>(null)
const documents = ref<Document[]>([])
const selectedIds = ref<number[]>([])
const chats = ref<Record<number, Chat>>({})
const view = ref<'research' | 'timetable' | 'school'>('research')
const activityOpen = ref(false)
const historyLoading = ref(false)
const schoolTools = ref(false)
const allowSync = ref(false)
const allowActions = ref(false)
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
const filter = ref('')
const query = ref('')
const asking = ref(false)
const uploading = ref(false)
const recognizing = ref(false)
const loadingDocs = ref(false)
const projectLoading = ref(false)
const sidebarOpen = ref(false)
const mobileDocs = ref(false)
const error = ref('')
const notice = ref('')
const connection = ref<'connecting' | 'online' | 'offline'>('connecting')
const modal = ref<'login' | 'register' | 'reset' | 'project' | 'help' | null>(null)
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
const recall = ref(40)
const excerpt = ref('')
const excerptDocument = ref<number | null>(null)
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
const processingCount = computed(
  () => documents.value.filter((d) => ['queued', 'processing'].includes(d.status)).length,
)
const currentChat = computed(() =>
  activeId.value === null ? undefined : chats.value[activeId.value],
)
const anyBusy = computed(
  () => asking.value || uploading.value || recognizing.value || modalBusy.value,
)
const canAsk = computed(
  () =>
    !!user.value &&
    !!activeId.value &&
    !!query.value.trim() &&
    (readyDocs.value.length > 0 ||
      (schoolTools.value && !selectedIds.value.length && !excerptDocument.value)) &&
    !historyLoading.value &&
    !asking.value &&
    !agentImageUploading.value &&
    (!agentImage.value || (schoolTools.value && !selectedIds.value.length && !excerptDocument.value)) &&
    !loadingDocs.value &&
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
function openCitation(citation: Source, text?: string) {
  source.value = { source: citation, text: text || '未返回原文片段' }
  mobileDocs.value = true
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
function openCitationGroup(group: CitationGroup) {
  const text = group.items
    .map((item) => item.text)
    .filter(Boolean)
    .join('\n\n—— 同一文档的另一片段 ——\n\n')
  openCitation(group.items[0].citation, text || '未返回原文片段')
}
const activeTurn = ref(0)
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
  schoolTools.value = false
  allowSync.value = false
  allowActions.value = false
  agentImage.value = null
  docRequest++
  clearTimeout(pollTimer)
  user.value = null
  projects.value = []
  documents.value = []
  activeId.value = null
  chats.value = {}
  selectedIds.value = []
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
    if (!activeId.value) activeId.value = projects.value[0]?.id ?? null
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
    const result = await request<Document[]>(`/projects/${id}/documents`)
    if (sequence !== docRequest || id !== activeId.value) return
    documents.value = result
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
  }
})
function chooseProject(id: number) {
  activeId.value = id
  view.value = 'research'
  sidebarOpen.value = false
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
function useSuggestion(text: string) {
  if (!user.value) return openModal('login')
  if (!activeId.value) return newProject()
  query.value = text
  composer.value?.focus()
}
async function scrollChat() {
  await nextTick()
  messagesEnd.value?.scrollIntoView({ behavior: 'smooth', block: 'end' })
}
async function ask() {
  if (!canAsk.value || !activeId.value) return
  if (
    excerptDocument.value &&
    selectedIds.value.length &&
    !selectedIds.value.includes(excerptDocument.value)
  ) {
    error.value = '原文所在文档需要包含在已选资料中。'
    return
  }
  const id = activeId.value
  const version = sessionVersion
  const chat = chats.value[id]!
  const text = query.value.trim()
  const turn: Turn = { question: text }
  const turnIndex = chat.turns.push(turn) - 1
  const body = {
    query: text,
    conversation_id: chat.conversationId ?? null,
    candidate_k_percentage: recall.value / 100,
    ...(schoolTools.value
      ? {
          use_school_tools: true,
          allow_school_sync: allowSync.value,
          allow_school_actions: allowActions.value,
          ...(agentImage.value ? { timetable_image_id: agentImage.value.id } : {}),
        }
      : {}),
    ...(selectedIds.value.length ? { doc_ids: [...selectedIds.value] } : {}),
    ...(excerptDocument.value
      ? { document_id: excerptDocument.value, selected_text: excerpt.value.trim() }
      : {}),
  }
  query.value = ''
  asking.value = true
  error.value = ''
  void scrollChat()
  try {
    if (durableAsk.value) {
      const result = await post<Job>(`/projects/${id}/ask/jobs`, body)
      if (version !== sessionVersion) return
      chat.turns[turnIndex]!.jobId = result.job_id
      await pollAnswer(id, chat, turnIndex, result.job_id, version)
    } else {
      const result = await post<Answer>(`/projects/${id}/ask`, body)
      if (version !== sessionVersion) return
      chat.turns[turnIndex]!.result = result
      chat.conversationId = result.conversation_id
    }
    if (version === sessionVersion && chat.turns[turnIndex]?.result?.tool_results?.some(
      item => item.tool === 'supplement_timetable_image',
    )) agentImage.value = null
  } catch (e) {
    if (version !== sessionVersion) return
    chat.turns[turnIndex]!.error = e instanceof Error ? e.message : '回答失败'
    if (e instanceof ApiError && e.status === 401) report(e)
  } finally {
    if (version === sessionVersion) asking.value = false
    void scrollChat()
  }
}
async function pollAnswer(id: number, chat: Chat, index: number, jobId: string, version: number) {
  for (let attempt = 0; attempt < 180; attempt++) {
    const job = await request<Job>(`/projects/${id}/jobs/${jobId}`)
    if (version !== sessionVersion) return
    if (job.status === 'completed' && job.result) {
      chat.turns[index]!.result = job.result
      chat.conversationId = job.result.conversation_id
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
  if (!activeId.value || asking.value) return
  const id = activeId.value,
    version = sessionVersion,
    sequence = ++historyRequest
  historyLoading.value = true
  try {
    const messages: HistoryMessage[] = []
    let offset = 0,
      total = 1
    while (offset < total) {
      const result = await request<PageResult<HistoryMessage>>(
        `/projects/${id}/conversations/${conversationId}/messages?limit=100&offset=${offset}`,
      )
      if (version !== sessionVersion || id !== activeId.value || sequence !== historyRequest) return
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
async function loadLatestConversation(id: number) {
  const version = sessionVersion
  try {
    const result = await request<PageResult<{ id: number }>>(
      `/projects/${id}/conversations?limit=1&offset=0`,
    )
    if (version !== sessionVersion || id !== activeId.value) return
    if (result.items.length) await openConversation(result.items[0].id)
  } catch {
    // 后台预载失败时保持欢迎页，不打断进入项目
  }
}
function newChat() {
  if (activeId.value && !asking.value && !historyLoading.value) {
    chats.value[activeId.value] = { turns: [] }
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
  <div class="app-shell">
    <button
      v-if="sidebarOpen"
      class="sidebar-scrim"
      aria-label="关闭导航"
      @click="sidebarOpen = false"
    ></button>
    <aside class="sidebar" :class="{ 'is-open': sidebarOpen }">
      <a class="brand" href="#" @click.prevent="view = 'research'"
        ><span class="brand-mark"><GraduationCap :size="23" /></span
        ><span>Academic<span class="brand-agent">Agent</span></span></a
      >
      <div class="workspace-label">个人工作空间 <ChevronDown :size="13" /></div>
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
      <div class="section-caption">
        我的项目 <span>{{ projects.length.toString().padStart(2, '0') }}</span>
      </div>
      <label class="project-search"
        ><Search :size="15" /><input v-model="filter" placeholder="搜索项目" aria-label="搜索项目"
      /></label>
      <div class="project-list">
        <p v-if="booting || projectLoading" class="sidebar-hint">正在加载项目…</p>
        <button
          v-for="project in visibleProjects"
          :key="project.id"
          class="project-item"
          :class="{ selected: activeId === project.id }"
          @click="chooseProject(project.id)"
        >
          <FolderOpen :size="16" /><span>{{ project.name }}</span
          ><ChevronRight :size="13" />
        </button>
        <p v-if="!booting && !projectLoading && !visibleProjects.length" class="sidebar-hint">
          {{ filter ? '没有匹配的项目' : '每一个研究，从一个项目开始。' }}
        </p>
      </div>
      <div class="sidebar-bottom">
        <div class="sidebar-note">
          <span class="note-symbol">✳</span><strong>让知识，彼此连接。</strong>
          <p>从阅读到理解，从灵感到发现。</p>
          <span class="note-line"></span>
        </div>
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
          <span class="connection" :class="connection"
            ><i></i
            >{{
              connection === 'online'
                ? '服务已连接'
                : connection === 'offline'
                  ? '服务未连接'
                  : '连接中'
            }}</span
          ><button v-if="connection === 'offline'" class="text-button" @click="bootstrap">
            重试</button
          ><span class="topbar-divider"></span><span class="version">ACADEMIC AGENT</span>
        </div>
      </header>
      <div v-if="error" class="banner error" role="alert">
        <span>{{ error }}</span
        ><button class="icon-button" aria-label="关闭错误提示" @click="error = ''">
          <X :size="16" />
        </button>
      </div>
      <div v-if="notice" class="banner success" role="status">
        <Check :size="16" /><span>{{ notice }}</span
        ><button class="icon-button" aria-label="关闭提示" @click="notice = ''">
          <X :size="16" />
        </button>
      </div>

      <div v-if="view === 'research'" class="research-layout">
        <main class="research-main">
          <div class="research-toolbar">
            <div>
              <span class="tiny-label">RESEARCH SPACE</span>
              <h1>{{ activeProject?.name || '研究工作台' }}</h1>
            </div>
            <div class="toolbar-actions">
              <button
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
            <span
              v-for="(turn, index) in currentChat.turns"
              :key="index"
              class="turn-dot-wrap"
              @click="scrollToTurn(index)"
            >
              <i class="turn-dot" :class="{ active: index === activeTurn }"></i>
              <span class="turn-dot-tip" role="tooltip">{{ turnPreview(turn.question) }}</span>
            </span>
          </nav>
          <div class="chat-scroll" @scroll.passive="onChatScroll">
            <ProjectActivity
              v-if="activityOpen && activeId && user"
              :key="`${user.id}:${activeId}`"
              :project-id="activeId"
              :locked="asking || historyLoading"
              @error="report"
              @conversation="openConversation"
              @refresh="loadDocuments(activeId)"
            />
            <p v-if="historyLoading" class="feature-notice" role="status">正在恢复历史会话…</p>
            <section v-if="!currentChat?.turns.length" class="welcome">
              <div class="welcome-emblem">
                <BookOpen :size="30" :stroke-width="1.6" /><span><Sparkles :size="13" /></span>
              </div>
              <div class="eyebrow">YOUR KNOWLEDGE, CONNECTED</div>
              <h2>让每一次提问，<br />都有知识作答<span>。</span></h2>
              <p class="welcome-description">
                把论文、笔记与灵感放在一起。<br />与你的资料对话，发现值得深入的下一步。
              </p>
              <div v-if="!user || !activeProject" class="start-action">
                <button
                  class="primary-button"
                  :disabled="booting"
                  @click="user ? newProject() : openModal('login')"
                >
                  {{ user ? '创建第一个项目' : '登录，开启研究' }}<ArrowRight :size="16" /></button
                ><span>你的专属学术知识空间</span>
              </div>
              <div v-else class="project-progress">
                <span :class="{ done: readyDocs.length }"
                  ><Check v-if="readyDocs.length" :size="13" /><span v-else>1</span>
                  上传研究资料</span
                ><i></i><span><span>2</span> 开始探索与提问</span>
              </div>
              <div class="suggestions">
                <button
                  v-for="item in suggestions"
                  :key="item.title"
                  @click="useSuggestion(item.query)"
                >
                  <component :is="item.icon" :size="21" :stroke-width="1.6" /><strong
                    >{{ item.title }}<ArrowRight :size="14" /></strong
                  ><span>{{ item.text }}</span>
                </button>
              </div>
              <p class="welcome-footnote"><span></span>基于你的资料回答 · 引用来源清晰可查</p>
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
                      v-html="markdown(turn.result.answer)"
                    ></div>
                    <p v-else-if="turn.error" class="turn-error">
                      {{ turn.error }}
                      <button class="text-button" @click="editQuestion(turn.question)">
                        重新编辑
                      </button>
                    </p>
                    <p v-else class="thinking">
                      <LoaderCircle :size="16" class="spin" />正在检索资料、整理回答…
                    </p>
                    <p v-if="turn.jobId" class="job-id">
                      任务 {{ turn.jobId }} · 刷新后可从“历史与任务”找回
                    </p>
                    <div v-if="turn.result?.sources.length" class="source-chips">
                      <button
                        v-for="group in citationGroups(turn)"
                        :key="group.items[0].citation.index"
                        @click="openCitationGroup(group)"
                      >
                        <span>{{ group.items[0].citation.index }}</span
                        ><FileText :size="13" />{{ group.filename
                        }}<em v-if="group.items.length > 1">×{{ group.items.length }}</em>
                      </button>
                    </div>
                  </div>
                </div>
              </article>
              <div ref="messagesEnd"></div>
            </section>
          </div>

          <div class="composer-wrap">
            <div v-if="settingsOpen" class="ask-settings">
              <label
                >检索范围比例 <strong>{{ recall }}%</strong
                ><input v-model.number="recall" type="range" min="5" max="100" step="5" /></label
              ><label
                >原文追问（可选）<select v-model="excerptDocument">
                  <option :value="null">不附带原文</option>
                  <option v-for="doc in readyDocs" :key="doc.id" :value="doc.id">
                    {{ doc.filename }}
                  </option>
                </select></label
              ><label v-if="excerptDocument"
                >粘贴要讨论的原文<textarea
                  v-model="excerpt"
                  maxlength="10000"
                  rows="3"
                  placeholder="从对应文档复制一段原文…"
                ></textarea>
              </label>
            </div>
            <div class="school-tool-settings">
              <label class="check-label"
                ><input v-model="schoolTools" type="checkbox" :disabled="asking" />学校助手</label
              >
              <template v-if="schoolTools"
                ><label class="check-label"
                  ><input
                    v-model="allowSync"
                    type="checkbox"
                    :disabled="asking"
                  />允许同步课程</label
                ><label class="check-label"
                  ><input
                    v-model="allowActions"
                    type="checkbox"
                    :disabled="asking"
                  />允许下载、导入、已读和课表操作</label
                ></template
              >
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
              <small v-if="schoolTools"
                >仅按本轮明确要求执行。课表操作包含学校获取、图片补充、增删改；学校密码请在弹出的学校窗口输入。选定文档或附带原文时优先文档问答。</small
              >
            </div>
            <form class="composer" @submit.prevent="ask">
              <div v-if="schoolTools" class="field-row">
                <input ref="agentImageInput" class="visually-hidden" type="file" accept="image/png,image/jpeg" aria-label="附加课表图片" @change="attachAgentImage" />
                <button type="button" class="text-button" :disabled="asking || agentImageUploading || !!selectedIds.length || !!excerptDocument" @click="agentImageInput?.click()">{{ agentImageUploading ? '正在上传图片…' : '附加课表图片' }}</button>
                <span v-if="agentImage">{{ agentImage.name }} · 本轮课表附件</span>
                <button v-if="agentImage" type="button" class="text-button" :disabled="asking || agentImageUploading" @click="agentImage = null">移除图片附件</button>
              </div>
              <textarea
                ref="composer"
                v-model="query"
                :disabled="!user || !activeId"
                maxlength="10000"
                rows="2"
                :placeholder="
                  !user
                    ? '登录后，开启与你的资料的第一场对话…'
                    : !activeId
                      ? '创建项目，开始整理你的研究资料…'
                      : schoolTools && !selectedIds.length && !excerptDocument
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
                <span class="scope-label"
                  ><Layers3 :size="14" />{{
                    selectedIds.length ? `已选 ${selectedIds.length} 份资料` : '全部项目资料'
                  }}<span v-if="excerptDocument" class="excerpt-label">附带原文</span></span
                >
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
              回答由 AI 生成，请结合引用原文核实。<span>Enter 发送 · Shift + Enter 换行</span>
            </p>
          </div>
        </main>

        <aside class="documents-panel" :class="{ 'mobile-visible': mobileDocs }">
          <div class="panel-heading">
            <h2>
              <FileText :size="17" />项目资料 <span>{{ documents.length }}</span>
            </h2>
            <button
              class="icon-button panel-close"
              aria-label="关闭资料栏"
              @click="mobileDocs = false"
            >
              <X :size="18" />
            </button>
          </div>
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
              ><strong>{{ uploading ? '正在上传…' : '点击或拖拽上传' }}</strong
              ><span>PDF、DOCX、PPTX、TXT</span>
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
            <div class="panel-footer"><span class="privacy-dot"></span>当前账号的独立项目空间</div>
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
        <div class="tiny-label">YOUR WEEK, ORGANIZED</div>
        <h1>把课表，整理成清晰的一周。</h1>
        <p class="page-description">从学校保存课表，用图片补充缺漏，也可以手动新增或修正上课安排。</p>
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

    <dialog
      ref="dialog"
      class="modal"
      @cancel.prevent="closeModal"
      @click="
        (event) => {
          if (event.target === dialog) closeModal()
        }
      "
    >
      <div class="modal-content">
        <button
          class="icon-button modal-close"
          :disabled="modalBusy"
          aria-label="关闭弹窗"
          @click="closeModal"
        >
          <X :size="19" /></button
        ><span class="modal-mark"><GraduationCap :size="25" /></span>
        <template v-if="modal === 'login' || modal === 'register' || modal === 'reset'"
          ><h2>
            {{
              modal === 'login'
                ? '欢迎回到你的知识空间'
                : modal === 'reset'
                  ? '重置应用密码'
                  : '开启你的学术工作空间'
            }}
          </h2>
          <p>整理资料，让每一次探索都有迹可循。</p>
          <form @submit.prevent="authenticate">
            <label v-if="modal === 'register'"
              >用户名<input
                v-model="username"
                required
                maxlength="20"
                autocomplete="username"
                placeholder="如何称呼你" /></label
            ><label
              >{{ modal === 'login' ? '邮箱或手机号' : '邮箱'
              }}<input
                :type="modal === 'login' ? 'text' : 'email'"
                v-model="account"
                required
                maxlength="255"
                autocomplete="username"
                :placeholder="modal === 'login' ? '输入邮箱或手机号' : '输入邮箱地址'" /></label
            ><label
              >密码<input
                v-model="password"
                type="password"
                required
                :minlength="modal === 'login' ? 1 : 8"
                maxlength="128"
                :autocomplete="modal === 'login' ? 'current-password' : 'new-password'"
                :placeholder="modal === 'login' ? '输入密码' : '至少 8 位字符'"
            /></label>
            <label v-if="modal === 'register'">手机号（选填）
              <input v-model="phone" type="tel" autocomplete="tel" maxlength="16" pattern="[+]?[0-9]{7,15}" placeholder="可留空，如 13800138000" />
            </label>
            <template v-if="modal !== 'login'">
              <label
                >确认密码<input
                  v-model="confirmPassword"
                  type="password"
                  required
                  minlength="8"
                  maxlength="128"
                  autocomplete="new-password"
              /></label>
              <label
                >邮箱验证码<input
                  v-model="emailCode"
                  required
                  inputmode="numeric"
                  pattern="[0-9]{6}"
                  maxlength="6"
                  autocomplete="one-time-code"
                  placeholder="6 位验证码"
              /></label>
              <button
                type="button"
                class="text-button"
                :disabled="codeBusy || modalBusy || !account.trim()"
                @click="sendEmailCode"
              >
                {{ codeBusy ? '正在发送…' : '发送验证码' }}
              </button>
              <p v-if="codeNotice" role="status">{{ codeNotice }}</p>
            </template>
            <p v-if="modalError" class="form-error" role="alert">{{ modalError }}</p>
            <button class="primary-button full-width" :disabled="modalBusy">
              <LoaderCircle v-if="modalBusy" class="spin" :size="16" />{{
                modalBusy
                  ? '请稍候…'
                  : modal === 'login'
                    ? '登录'
                    : modal === 'reset'
                      ? '重置密码'
                      : '创建账号'
              }}<ArrowRight v-if="!modalBusy" :size="16" />
            </button>
          </form>
          <button
            v-if="modal === 'login'"
            class="text-button"
            :disabled="modalBusy"
            @click="openModal('reset')"
          >
            忘记密码？
          </button>
          <div class="auth-switch">
            {{ modal === 'login' ? '还没有账号？' : '已经有账号？'
            }}<button
              class="text-button"
              :disabled="modalBusy"
              @click="openModal(modal === 'login' ? 'register' : 'login')"
            >
              {{ modal === 'login' ? '立即注册' : '去登录' }}
            </button>
          </div></template
        >
        <template v-else-if="modal === 'project'"
          ><h2>一个项目，一次新的探索。</h2>
          <p>按课程、论文主题或研究方向整理你的资料。</p>
          <form @submit.prevent="createProject">
            <label
              >项目名称<input
                v-model="projectName"
                required
                maxlength="100"
                placeholder="例如：大语言模型文献研究"
                autofocus
            /></label>
            <p v-if="modalError" class="form-error" role="alert">{{ modalError }}</p>
            <button class="primary-button full-width" :disabled="modalBusy || !projectName.trim()">
              <LoaderCircle v-if="modalBusy" class="spin" :size="16" />创建项目<ArrowRight
                :size="16"
              />
            </button></form
        ></template>
        <template v-else-if="modal === 'help'"
          ><h2>从资料到理解，只需三步。</h2>
          <ol class="help-steps">
            <li>
              <strong>创建研究项目</strong>
              <p>注册或登录后，为一个研究主题建立项目。</p>
            </li>
            <li>
              <strong>上传你的资料</strong>
              <p>支持 PDF、TXT、DOCX 和 PPTX。等待状态变为“已就绪”后即可提问。</p>
            </li>
            <li>
              <strong>提问、追问与核实</strong>
              <p>
                在同一对话内继续追问，点击引用查看证据。右侧勾选文件可缩小范围，问答设置中可以附带原文。
              </p>
            </li>
          </ol>
          <p>
            刷新后可从“历史与任务”打开已保存的会话并继续提问，也可导出
            Markdown。后台问答任务可在任务列表中查看结果或重试。
          </p>
          <button class="primary-button full-width" @click="closeModal">
            开始探索 <ArrowRight :size="16" /></button
        ></template>
      </div>
    </dialog>
  </div>
</template>
