export interface User {
  id: number
  username: string
}
export interface Project {
  id: number
  name: string
}
export interface Document {
  image_summary?: { total: number; failed: number; failures: { picture_id: string; pages: number[]; reason: string }[] } | null
  id: number
  filename: string
  status: 'queued' | 'processing' | 'completed' | 'failed'
}
export interface Source {
  index: number
  document_id: number
  parent_id: number
  filename: string
  section_id?: string | null
  heading_path?: string | null
  page_numbers?: number[] | null
  child_page_numbers?: number[] | null
  /** 命中的子块原文：比父块更精确，高亮定位优先用它 */
  child_content?: string | null
  /** 回答实际依据的引文 */
  quote_text?: string | null
}
/** 传给文档预览器的高亮目标：引用编号 + 被引原文 + 大致页码 */
export interface CitationHighlight {
  index: number
  text: string
  pages?: number[]
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
    public code?: string,
  ) {
    super(message)
  }
}

export async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${url}`, { ...init, credentials: 'same-origin' })
  } catch (e) {
    // 主动中止（用户点“停止”）不是网络故障，原样抛出让调用方识别
    if (e instanceof DOMException && e.name === 'AbortError') throw e
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
    throw new ApiError(response.status, message, detail?.code)
  }
  return payload as T
}

export const post = <T>(url: string, body: unknown, signal?: AbortSignal) =>
  request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  })

/** 文件内 AI 批注（docx 批注 / pdf 高亮 / pptx 备注） */
export interface DocumentAnnotation {
  id: number
  document_id: number
  cite_index: number
  summary: string
  kind: 'docx_comment' | 'pdf_note' | 'pdf_highlight' | 'pptx_note'
  pages: number[]
  preview?: string
  created_at?: string | null
}
export interface AnnotationResult extends DocumentAnnotation {
  total: number
  created: boolean
  filename: string
  download_url: string
}
export interface AnnotationList {
  items: DocumentAnnotation[]
  total: number
  filename: string
  download_url: string
  has_copy: boolean
}

export function addAnnotation(
  projectId: number,
  documentId: number,
  body: {
    cite_index: number
    anchor_text: string
    question?: string
    answer?: string
    pages?: number[]
    message_id?: number
    turn_key?: string
  },
) {
  return post<AnnotationResult>(`/projects/${projectId}/documents/${documentId}/annotations`, body)
}

export function listAnnotations(projectId: number, documentId: number) {
  return request<AnnotationList>(`/projects/${projectId}/documents/${documentId}/annotations`)
}

export function deleteAnnotation(projectId: number, documentId: number, annotationId: number) {
  return request<{ removed: number; deleted: boolean }>(
    `/projects/${projectId}/documents/${documentId}/annotations/${annotationId}`,
    { method: 'DELETE' },
  )
}

export const annotatedFileUrl = (projectId: number, documentId: number) =>
  `/api/projects/${projectId}/documents/${documentId}/annotated`
