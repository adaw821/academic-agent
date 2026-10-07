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
