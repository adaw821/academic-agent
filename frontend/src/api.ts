export interface User {
  id: number
  username: string
}
export interface Project {
  id: number
  name: string
}
export interface Document {
  id: number
  filename: string
  status: 'queued' | 'processing' | 'completed' | 'failed'
}
export interface Source {
  index: number
  document_id: number
  parent_id: number
  filename: string
}
export interface Answer {
  answer: string
  conversation_id: number
  sources: Source[]
  retrieved_contexts: string[]
  tool_results?: Record<string, unknown>[]
}
export interface PageResult<T> {
  items: T[]
  total: number
  limit?: number
  offset?: number
}
export interface Job {
  job_id: string
  kind: 'qa' | 'document'
  document_id: number | null
  status: 'queued' | 'running' | 'completed' | 'failed'
  error_type?: string | null
  result?: Answer | null
}
export interface HistoryMessage {
  id: number
  role: string
  content: string
  turn_id: number | string
  response_data: Partial<Answer> | null
}

export const statusLabel = (value: string) =>
  ({
    queued: '排队中',
    running: '处理中',
    processing: '处理中',
    completed: '已完成',
    failed: '失败',
    pending: '待提交',
    submitted: '已提交',
    unknown: '尚未确认',
    available: '本次可见',
    seen: '本次可见',
    not_seen: '最近未发现',
  })[value] || value
export const localTime = (value?: string | null) =>
  value
    ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
    : '未提供'
export function safeLink(value?: string | null) {
  if (!value) return undefined
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined
  } catch {
    return undefined
  }
}
export interface Course {
  course_name: string
  course_code: string | null
  day_of_week: string | number
  start_time: string
  end_time: string
  location: string | null
  teachers: string | null
  units: number | null
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${url}`, { ...init, credentials: 'same-origin' })
  } catch {
    throw new ApiError(0, '无法连接服务，请检查网络与后端运行状态。')
  }
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail
    const message =
      typeof detail === 'string'
        ? detail
        : typeof detail?.message === 'string'
          ? detail.message
          : Array.isArray(detail)
            ? detail.map((item: { msg: string }) => item.msg).join('；')
            : response.status >= 500
              ? '服务暂时不可用，请确认后端已启动后重试。'
              : `请求失败（${response.status}）`
    throw new ApiError(response.status, message)
  }
  return payload as T
}

export const post = <T>(url: string, body: unknown) =>
  request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
