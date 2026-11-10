<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import {
  request,
  ApiError,
  post,
  localTime,
  safeLink,
  statusLabel,
  type PageResult,
  type Project,
} from '../api'
const props = defineProps<{ projects: Project[]; projectId: number | null }>()
const emit = defineEmits<{ error: [unknown]; imported: [number] }>()
interface SchoolCourse {
  id: number
  course_name: string
  source_url: string | null
  last_synced_at: string | null
}
interface Assignment {
  id: number
  course_id: number
  course_name: string
  title: string
  status: string
  source_status: string
  due_at: string | null
  last_synced_at: string | null
  source_url: string
}
interface Resource {
  id: number
  title: string
  resource_type: string
  purpose: string
  source_status: string
  source_url: string
  content?: string
}
interface Reminder {
  id: number
  title: string
  course_name: string
  due_at: string
  current_due_at: string | null
  active: boolean
  read_at: string | null
}
const tab = ref<'courses' | 'assignments' | 'reminders'>('courses')
const busy = ref(false),
  loading = ref(false),
  message = ref(''),
  schoolAccount = ref(''),
  schoolPassword = ref(''),
  configured = ref<boolean | null>(null)
const loginPending = ref(false)
const loginMessage = ref('')
const connectionState = ref('')
let loginTimer: ReturnType<typeof setTimeout> | undefined
interface LoginStatus { connected: boolean; state: string | null; message: string | null }
function applyLoginStatus(result: LoginStatus) {
  if (!alive) return
  loginMessage.value = result.message || ''
  configured.value = result.connected
  connectionState.value = result.state === 'authentication_required' ? '需要更新学校账密' : ''
  loginPending.value = ['queued', 'running'].includes(result.state || '')
  clearTimeout(loginTimer)
  if (loginPending.value) loginTimer = setTimeout(() => void pollLogin(), 3000)
}
async function pollLogin() {
  try {
    applyLoginStatus(await request<LoginStatus>('/school/account'))
    if (!loginPending.value) await load()
  } catch (e) {
    if (!alive) return
    loginPending.value = false
    loginMessage.value = '暂时无法读取登录进度，请重试连接以恢复。'
    fail(e)
  }
}
function retrySync() {
  void act(async () => {
    await post('/school/account/sync', {})
    loginMessage.value = '已请求同步，正在检查进度…'
    clearTimeout(loginTimer)
    loginTimer = setTimeout(() => void pollLogin(), 1500)
  })
}
const coursePage = ref<PageResult<SchoolCourse>>({ items: [], total: 0 }),
  courseOffset = ref(0)
const discovered = ref<{ title: string; source_url: string }[]>([]),
  discoveryWarning = ref(''),
  discoveryDone = ref(false)
const sourceUrl = ref(''),
  course = ref<SchoolCourse | null>(null),
  resources = ref<PageResult<Resource>>({ items: [], total: 0 }),
  resourceOffset = ref(0)
const assignmentScope = ref<number | null>(null)
const downloaded = ref<Record<number, boolean>>({}),
  importStates = ref<Record<string, string>>({})
const targetProject = ref<number | null>(props.projectId)
const assignmentPage = ref<PageResult<Assignment>>({ items: [], total: 0 }),
  assignmentOffset = ref(0)
const submission = ref(''),
  visibility = ref(''),
  dueFrom = ref(''),
  dueTo = ref('')
const reminderPage = ref<PageResult<Reminder> & { unread_count?: number }>({ items: [], total: 0 }),
  reminderOffset = ref(0)
const unread = ref(true),
  activeOnly = ref(true)
let alive = true,
  generation = 0
onUnmounted(() => {
  clearTimeout(loginTimer)
  alive = false
  generation++
})
watch(
  () => props.projectId,
  (id) => {
    targetProject.value = id
  },
)
function fail(e: unknown) {
  if (alive) {
    if (e instanceof ApiError && e.code === 'school_login_required') {
      connectionState.value = '登录已过期，请重新连接'
    }
    emit('error', e)
  }
}
async function act(work: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  message.value = ''
  try {
    await work()
  } catch (e) {
    fail(e)
  } finally {
    busy.value = false
  }
}
async function load() {
  const token = ++generation
  loading.value = true
  try {
    if (tab.value === 'courses') {
      const result = await request<PageResult<SchoolCourse>>(
        `/courses?limit=20&offset=${courseOffset.value}`,
      )
      if (alive && token === generation) coursePage.value = result
    } else if (tab.value === 'assignments') {
      const params = new URLSearchParams({ limit: '20', offset: String(assignmentOffset.value) })
      if (submission.value) params.set('status', submission.value)
      if (visibility.value) params.set('source_status', visibility.value)
      if (dueFrom.value) params.set('due_from', dueFrom.value)
      if (dueTo.value) params.set('due_to', dueTo.value)
      const result = await request<PageResult<Assignment>>(`/assignments?${params}`)
      if (alive && token === generation) assignmentPage.value = result
    } else {
      const result = await request<typeof reminderPage.value>(
        `/assignment-reminders?active_only=${activeOnly.value}&unread_only=${unread.value}&limit=20&offset=${reminderOffset.value}`,
      )
      if (alive && token === generation) reminderPage.value = result
    }
  } catch (e) {
    if (token === generation) fail(e)
  } finally {
    if (token === generation) loading.value = false
  }
}
watch(tab, () => {
  course.value = null
  void load()
})
onMounted(async () => {
  void load()
  await pollLogin()
})
function connect() {
  void act(async () => {
    try {
      await request('/school/account', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ school_account: schoolAccount.value.trim(), school_password: schoolPassword.value }),
      })
      configured.value = true
      connectionState.value = '已连接'
      message.value = '学校连接已验证并保存。'
      await pollLogin()
    } finally {
      schoolPassword.value = ''
    }
  })
}
function disconnect() {
  void act(async () => {
    await request('/school/account', { method: 'DELETE' })
    clearTimeout(loginTimer)
    loginPending.value = false
    configured.value = false
    connectionState.value = ''
    loginMessage.value = ''
    schoolPassword.value = ''
    discovered.value = []
    discoveryDone.value = false
    message.value = '学校连接已解除，已同步数据仍保留。'
  })
}
function discover() {
  void act(async () => {
    const result = await request<{
      courses: typeof discovered.value
      complete: boolean
      warning: string | null
    }>('/school/courses')
    discovered.value = result.courses
    discoveryDone.value = true
    discoveryWarning.value = result.complete
      ? ''
      : result.warning || '课程发现不完整，可粘贴课程链接导入。'
  })
}
function sync(item?: SchoolCourse, url = sourceUrl.value) {
  void act(async () => {
    const result = await post<{ assignments: number; resources: number; warnings: string[] }>(
      item ? `/courses/${item.id}/sync` : '/courses/sync',
      item ? {} : { source_url: url.trim() },
    )
    message.value = `同步完成：${result.assignments} 份作业、${result.resources} 项资源。${result.warnings.join('；')}`
    await load()
  })
}
let resourceGeneration = 0
async function loadResources() {
  if (!course.value) return
  const token = ++resourceGeneration,
    id = course.value.id
  const params = new URLSearchParams({ limit: '20', offset: String(resourceOffset.value) })
  if (assignmentScope.value !== null) params.set('assignment_id', String(assignmentScope.value))
  const result = await request<PageResult<Resource>>(`/courses/${id}/resources?${params}`)
  if (alive && resourceGeneration === token && course.value?.id === id) resources.value = result
}
function openResources(item: SchoolCourse, assignmentId: number | null = null) {
  course.value = item
  assignmentScope.value = assignmentId
  resourceOffset.value = 0
  resources.value = { items: [], total: 0 }
  void act(loadResources)
}
const resourceTypes: Record<string, string> = {
  file: '文件',
  resource: '附件',
  folder: '文件夹',
  page: '页面',
  url: '链接',
}
function attachment(item: Resource) {
  return ['file', 'resource'].includes(item.resource_type) && item.purpose !== 'student_submission'
}
function download(item: Resource) {
  const id = course.value!.id
  void act(async () => {
    const result = await post<{ downloaded: boolean }>(
      `/courses/${id}/resources/${item.id}/download`,
      {},
    )
    downloaded.value[item.id] = result.downloaded
    message.value = '附件已保存到服务器，可导入知识库。'
  })
}
function inspectDownload(item: Resource) {
  const id = course.value!.id
  void act(async () => {
    const result = await request<{ downloaded: boolean }>(
      `/courses/${id}/resources/${item.id}/download`,
    )
    downloaded.value[item.id] = result.downloaded
    message.value = result.downloaded ? '该附件已有服务器缓存。' : '该附件尚未下载或缓存已丢失。'
  })
}
function importFile(item: Resource) {
  const projectId = targetProject.value,
    id = course.value!.id
  if (!projectId) return
  void act(async () => {
    const result = await post<{ status: string; reused: boolean }>(
      `/courses/${id}/resources/${item.id}/import`,
      { project_id: projectId },
    )
    importStates.value[`${projectId}:${item.id}`] = statusLabel(result.status)
    message.value = `导入${result.reused ? '已有任务' : '任务已创建'}：${statusLabel(result.status)}。可在对应项目的任务列表查看进度。`
    if (alive) emit('imported', projectId)
  })
}
function read(item: Reminder) {
  void act(async () => {
    await post(`/assignment-reminders/${item.id}/read`, {})
    message.value = '已标记为已读，不改变作业提交状态。'
    await load()
  })
}
</script>

<template>
  <main class="school-page feature-page">
    <div class="tiny-label">CAMPUS WORKSPACE</div>
    <h1>学校事务</h1>
    <p class="page-description">
      课程、作业与提醒集中查看。这里展示最近同步的数据，所有时间均为北京时间。
    </p>
    <details class="feature-card connection-card" open>
      <summary>
        学校连接 ·
        {{
          connectionState || (configured === null ? '正在检查' : configured ? '已保存连接' : '未连接')
        }}
      </summary>
      <p>学校凭据已加密保存，用于后台同步。删除连接会清除凭据与会话，已导入数据保留。</p>
      <div class="field-row">
        <button v-if="configured" class="primary-button" :disabled="busy" @click="retrySync">同步课程和课表 / 重试</button>
        <button v-if="configured" class="subtle-button" :disabled="busy" @click="disconnect">删除学校连接</button>
      </div>
      <p v-if="loginMessage" role="status">{{ loginMessage }}</p>
      <details>
        <summary>更新学校账密</summary>
      <form class="field-row" @submit.prevent="connect">
        <label>学生账号<input v-model="schoolAccount" autocomplete="off" maxlength="128" required /></label>
        <label>学校密码<input v-model="schoolPassword" type="password" autocomplete="off" maxlength="256" required /></label>
        <button class="primary-button" :disabled="busy">验证并更新连接</button>
      </form>
      </details>
    </details>
    <p v-if="message && !busy" class="feature-notice" role="status">{{ message }}</p>
    <p v-if="busy" class="feature-notice" role="status">正在处理，请稍候…</p>
    <nav class="feature-tabs" aria-label="学校事务分类">
      <button
        v-for="(label, key) in {
          courses: '课程与附件',
          assignments: '作业',
          reminders: '站内提醒',
        }"
        :key="key"
        :aria-pressed="tab === key"
        :disabled="busy"
        @click="tab = key"
      >
        {{ label }}
      </button>
      <button :disabled="busy || loading" @click="load">刷新列表</button>
    </nav>
    <p v-if="loading" role="status">正在读取…</p>
    <template v-else-if="tab === 'courses'">
      <div class="feature-card">
        <div class="field-row">
          <h2>发现学校课程</h2>
          <button class="subtle-button" :disabled="busy" @click="discover">从学校发现课程</button>
        </div>
        <p v-if="discoveryWarning" class="feature-warning">{{ discoveryWarning }}</p>
        <p v-if="discoveryDone && !discovered.length">本次未发现课程，可使用课程链接导入。</p>
        <div v-for="item in discovered" :key="item.source_url" class="compact-row">
          <span>{{ item.title }}</span
          ><button class="text-button" :disabled="busy" @click="sync(undefined, item.source_url)">
            同步到我的课程
          </button>
        </div>
        <form class="field-row" @submit.prevent="sync()">
          <label
            >学校课程链接<input
              v-model="sourceUrl"
              type="url"
              placeholder="https://…/course/view.php?id=…"
              required /></label
          ><button class="subtle-button" :disabled="busy || !sourceUrl.trim()">导入课程</button>
        </form>
      </div>
      <h2>已保存课程</h2>
      <p v-if="!coursePage.items.length" class="empty-feature">
        暂无本地课程，先同步学校课程或上传课表。
      </p>
      <article v-for="item in coursePage.items" :key="item.id" class="feature-card">
        <h3>
          {{ item.course_name }} <small>#{{ item.id }}</small>
        </h3>
        <p>最近同步：{{ localTime(item.last_synced_at) }}</p>
        <div class="field-row">
          <button class="subtle-button" :disabled="busy || !item.source_url" @click="sync(item)">
            同步课程</button
          ><button class="subtle-button" :disabled="busy" @click="openResources(item)">
            查看附件
          </button>
        </div>
      </article>
      <div class="pagination">
        <button
          :disabled="busy || courseOffset === 0"
          @click="
            () => {
              courseOffset -= 20
              load()
            }
          "
        >
          上一页课程</button
        ><span>共 {{ coursePage.total }} 门</span
        ><button
          :disabled="busy || courseOffset + 20 >= coursePage.total"
          @click="
            () => {
              courseOffset += 20
              load()
            }
          "
        >
          下一页课程
        </button>
      </div>
    </template>
    <template v-else-if="tab === 'assignments'">
      <form
        class="feature-card field-row"
        @submit.prevent="
          () => {
            assignmentOffset = 0
            load()
          }
        "
      >
        <label
          >提交状态<select v-model="submission">
            <option value="">全部</option>
            <option value="pending">待提交</option>
            <option value="submitted">已提交</option>
            <option value="unknown">尚未确认</option>
          </select></label
        >
        <label
          >学校可见状态<select v-model="visibility">
            <option value="">全部</option>
            <option value="available">本次可见</option>
            <option value="not_seen">最近未发现</option>
            <option value="unknown">尚未确认</option>
          </select></label
        >
        <label>截止起日<input v-model="dueFrom" type="date" /></label
        ><label>截止止日<input v-model="dueTo" type="date" :min="dueFrom" /></label
        ><button class="subtle-button" :disabled="busy">筛选作业</button>
      </form>
      <p class="feature-warning">“最近未发现”不代表已删除或无需提交，请以学校系统为准。</p>
      <p v-if="!assignmentPage.items.length" class="empty-feature">当前筛选下没有本地作业记录。</p>
      <article v-for="item in assignmentPage.items" :key="item.id" class="feature-card">
        <h3>{{ item.title }}</h3>
        <p>
          {{ item.course_name }} · {{ statusLabel(item.status) }} ·
          {{ statusLabel(item.source_status) }}
        </p>
        <p>截止：{{ localTime(item.due_at) }}</p>
        <p>最近采集：{{ localTime(item.last_synced_at) }}</p>
        <div class="field-row">
          <a
            v-if="safeLink(item.source_url)"
            class="text-button"
            :href="safeLink(item.source_url)"
            target="_blank"
            rel="noopener noreferrer"
            >在学校查看</a
          ><button
            class="subtle-button"
            :disabled="busy"
            @click="
              () => {
                openResources(
                  {
                    id: item.course_id,
                    course_name: item.course_name,
                    source_url: null,
                    last_synced_at: null,
                  },
                  item.id,
                )
              }
            "
          >
            查看作业附件
          </button>
        </div>
      </article>
      <div class="pagination">
        <button
          :disabled="busy || !assignmentOffset"
          @click="
            () => {
              assignmentOffset -= 20
              load()
            }
          "
        >
          上一页作业</button
        ><span>共 {{ assignmentPage.total }} 份</span
        ><button
          :disabled="busy || assignmentOffset + 20 >= assignmentPage.total"
          @click="
            () => {
              assignmentOffset += 20
              load()
            }
          "
        >
          下一页作业
        </button>
      </div>
    </template>
    <template v-else>
      <div class="field-row">
        <label class="check-label"
          ><input
            v-model="unread"
            type="checkbox"
            :disabled="busy"
            @change="
              () => {
                reminderOffset = 0
                load()
              }
            "
          />只看未读</label
        ><label class="check-label"
          ><input
            v-model="activeOnly"
            type="checkbox"
            :disabled="busy"
            @change="
              () => {
                reminderOffset = 0
                load()
              }
            "
          />只看当前有效</label
        ><span>该范围未读 {{ reminderPage.unread_count ?? 0 }} 条</span>
      </div>
      <p class="feature-warning">
        提醒由后台扫描生成；这里不会创建提醒。没有提醒不等于没有待交作业。
      </p>
      <p v-if="!reminderPage.items.length" class="empty-feature">当前筛选下没有提醒。</p>
      <article v-for="item in reminderPage.items" :key="item.id" class="feature-card">
        <h3>
          {{ item.title }} <small>#{{ item.id }}</small>
        </h3>
        <p>
          {{ item.course_name }} · {{ item.active ? '有效提醒' : '历史提醒' }} ·
          {{ item.read_at ? '已读' : '未读' }}
        </p>
        <p>提醒截止时间：{{ localTime(item.due_at) }}</p>
        <p>最新作业截止时间：{{ localTime(item.current_due_at) }}</p>
        <button class="subtle-button" :disabled="busy || !!item.read_at" @click="read(item)">
          标记已读
        </button>
      </article>
      <div class="pagination">
        <button
          :disabled="busy || !reminderOffset"
          @click="
            () => {
              reminderOffset -= 20
              load()
            }
          "
        >
          上一页提醒</button
        ><span>共 {{ reminderPage.total }} 条</span
        ><button
          :disabled="busy || reminderOffset + 20 >= reminderPage.total"
          @click="
            () => {
              reminderOffset += 20
              load()
            }
          "
        >
          下一页提醒
        </button>
      </div>
    </template>
    <section v-if="course" class="feature-card resource-panel" aria-label="课程附件">
      <div class="field-row">
        <h2>{{ course.course_name }} · {{ assignmentScope ? '作业附件' : '课程资源' }}</h2>
        <button
          class="text-button"
          :disabled="busy"
          @click="
            () => {
              course = null
              resourceGeneration++
            }
          "
        >
          关闭附件列表
        </button>
      </div>
      <label
        >导入到知识库<select v-model="targetProject">
          <option :value="null">选择项目</option>
          <option v-for="project in projects" :value="project.id" :key="project.id">
            {{ project.name }}
          </option>
        </select></label
      >
      <p v-if="!resources.items.length">暂无关联资源。</p>
      <article v-for="item in resources.items" :key="item.id" class="resource-row">
        <h3>
          {{ item.title }} <small>#{{ item.id }}</small>
        </h3>
        <p>
          {{ resourceTypes[item.resource_type] || '资源' }} · {{ statusLabel(item.source_status)
          }}<span v-if="downloaded[item.id] !== undefined">
            · {{ downloaded[item.id] ? '已有缓存' : '无缓存' }}</span
          >
        </p>
        <details v-if="item.content">
          <summary>已采集内容</summary>
          <pre class="plain-content">{{ item.content }}</pre>
        </details>
        <div class="field-row">
          <a
            v-if="safeLink(item.source_url)"
            class="text-button"
            :href="safeLink(item.source_url)"
            target="_blank"
            rel="noopener noreferrer"
            >学校原链接</a
          >
          <template v-if="attachment(item)"
            ><button class="subtle-button" :disabled="busy" @click="inspectDownload(item)">
              检查缓存</button
            ><button class="subtle-button" :disabled="busy" @click="download(item)">
              下载到服务器</button
            ><button
              class="subtle-button"
              :disabled="busy || !targetProject"
              @click="importFile(item)"
            >
              导入知识库
            </button></template
          >
        </div>
        <p v-if="targetProject && importStates[`${targetProject}:${item.id}`]">
          导入状态：{{ importStates[`${targetProject}:${item.id}`] }}（后续进度见项目任务）
        </p>
      </article>
      <div class="pagination">
        <button
          :disabled="busy || !resourceOffset"
          @click="
            () => {
              resourceOffset -= 20
              act(loadResources)
            }
          "
        >
          上一页附件</button
        ><span>共 {{ resources.total }} 项</span
        ><button
          :disabled="busy || resourceOffset + 20 >= resources.total"
          @click="
            () => {
              resourceOffset += 20
              act(loadResources)
            }
          "
        >
          下一页附件
        </button>
      </div>
    </section>
  </main>
</template>
