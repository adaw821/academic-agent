import type { CitationHighlight } from '../api'

/**
 * 引用高亮引擎：在已渲染的文档 DOM 里定位被引原文并包裹 <mark>。
 *
 * 设计取舍：
 * - 不改动渲染器本身（pdf.js / docx-preview / pptx-preview 都适用），只在渲染完成后处理 DOM。
 * - 匹配采用“空白归一化 + 忽略大小写”，因为解析器切出的文本与渲染层抽取的文本常有换行/空格差异。
 * - 定位采用“聚类”：同一处引用的多个片段应当挨在一起，因此在全文所有出现位置里
 *   选一个能覆盖最多片段的窗口，只在该窗口内高亮。避免重复文本（题面复述、解答区、
 *   目录）导致的高亮跑到第一次出现的地方。
 * - 命中不了时静默跳过：宁可少高亮，也不产生错误定位。
 */

const MAX_SEGMENTS = 8
const MAX_MARKS = 40 // 总 mark 节点上限：PDF 里一行会横跨多个 span，不能按节点数限制行数
const MAX_SEGMENT_CHARS = 200
const MIN_SEGMENT_CHARS = 8
const MIN_CLAUSE_CHARS = 10
const CLUSTER_WINDOW = 3000 // 聚类窗口（归一化字符数），约等于文档里 1~2 屏
const MAX_OCCURRENCES = 6 // 每个片段最多考察的出现次数
const MARK_CLASS = 'cite-hit'

/** 归一化：去掉所有空白 + 转小写（中英文都适用） */
function normalize(input: string): string {
  return input.replace(/\s+/g, '').toLowerCase()
}

/**
 * 清洗后端解析产物，使其与渲染后的正文文本对齐：
 * - "<!-- image -->" 等解析器注释不会出现在渲染结果里
 * - 行首列表符（- * •）和 markdown 标题符（#）在 docx/pptx 渲染时由样式生成，不是文本节点
 * - markdown 强调符（* `）是解析时加的标记，渲染结果里没有
 * - 数学公式被解析成 LaTeX（$customer=(\overline{cid},name)$），渲染后只剩纯文本
 */
function cleanChunkText(text: string): string {
  return text
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/^[ \t]*#+[ \t]*/gm, ' ')
    .replace(/^[ \t]*[-*•·][ \t]+/gm, ' ')
    .replace(/\\([_&%#{}$])/g, '$1')
    .replace(/\\[a-zA-Z]+\*?/g, ' ')
    .replace(/[$\\{}*`]/g, '')
}

/**
 * 把引用原文切成若干可独立匹配的片段。
 * 按"行"切而不是拼成长串：一行失配不影响其他行，渲染层与解析层的
 * 换行差异、页眉页脚等噪音只会废掉个别行，不会拖垮整段匹配。
 */
export function citationSegments(text: string): string[] {
  const cleaned = cleanChunkText((text || ''))
  const out: string[] = []
  for (const block of cleaned.split(/\n+/)) {
    const norm = normalize(block)
    if (norm.length < MIN_SEGMENT_CHARS) continue // 标题、孤立短语等太短的行
    if (norm.length <= MAX_SEGMENT_CHARS) {
      if (!out.includes(norm)) out.push(norm)
    } else {
      // 超长行（docx 整段）按句子切成 80~200 字的片段
      for (const part of splitLongBlock(norm)) {
        if (part.length >= MIN_SEGMENT_CHARS && !out.includes(part)) out.push(part)
      }
    }
    if (out.length >= MAX_SEGMENTS) break
  }
  return out.slice(0, MAX_SEGMENTS)
}

/** 超长行按句读切，累积到 80 字输出一段 */
function splitLongBlock(norm: string): string[] {
  const parts = norm.split(/(?<=[。！？；.!?;])/)
  const out: string[] = []
  let buf = ''
  for (const part of parts) {
    buf += part
    if (buf.length >= 80) {
      out.push(buf.slice(0, MAX_SEGMENT_CHARS))
      buf = ''
    }
  }
  if (buf && out.length < MAX_SEGMENTS) out.push(buf.slice(0, MAX_SEGMENT_CHARS))
  return out
}

/** 整段匹配失败时的兜底：把片段按逗号等次级标点再切成短句 */
function clauseSegments(segment: string): string[] {
  return segment
    .split(/[,;，；、()（）]/)
    .map((part) => part.trim())
    .filter((part) => part.length >= MIN_CLAUSE_CHARS)
    .slice(0, 6)
}

interface TextSpan {
  node: Text
  start: number // 原始文本内起始偏移
  end: number
}

function collectTextSpans(root: HTMLElement): TextSpan[] {
  const spans: TextSpan[] = []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = (node as Text).parentElement
      if (!parent) return NodeFilter.FILTER_REJECT
      const tag = parent.tagName
      if (tag === 'SCRIPT' || tag === 'STYLE') return NodeFilter.FILTER_REJECT
      if (parent.closest(`mark.${MARK_CLASS}`)) return NodeFilter.FILTER_REJECT
      const value = (node as Text).nodeValue
      if (!value || !value.trim()) return NodeFilter.FILTER_REJECT
      return NodeFilter.FILTER_ACCEPT
    },
  })
  let cursor = 0
  let node: Node | null
  while ((node = walker.nextNode())) {
    const text = node as Text
    const len = text.nodeValue?.length ?? 0
    spans.push({ node: text, start: cursor, end: cursor + len })
    cursor += len
  }
  return spans
}

interface Haystack {
  spans: TextSpan[]
  normalized: string
  map: number[] // 归一化字符下标 → 原始文本下标
}

/** 把容器的可见文本拼成一条归一化草堆，供定位使用 */
function buildHaystack(root: HTMLElement): Haystack {
  const spans = collectTextSpans(root)
  let raw = ''
  for (const span of spans) raw += span.node.nodeValue ?? ''
  const map: number[] = []
  let normalized = ''
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i]
    if (/\s/.test(ch)) continue
    normalized += ch.toLowerCase()
    map.push(i)
  }
  return { spans, normalized, map }
}

/** 找出 needle 在草堆里的全部出现位置（最多 limit 个） */
function occurrences(hay: string, needle: string, limit = MAX_OCCURRENCES): number[] {
  const out: number[] = []
  if (needle.length < MIN_SEGMENT_CHARS) return out
  let from = 0
  while (out.length < limit) {
    const at = hay.indexOf(needle, from)
    if (at < 0) break
    out.push(at)
    from = at + 1
  }
  return out
}

interface SegmentHit {
  needle: string // 实际用来匹配的文本（整段或降级后的短句）
  positions: number[] // 归一化下标
}

/** 为一个片段挑匹配文本：整段优先，失败再降级为逗号短句 */
function locateSegment(hay: string, needles: string[]): SegmentHit | null {
  for (const needle of needles) {
    const positions = occurrences(hay, needle)
    if (positions.length) return { needle, positions }
  }
  return null
}

/**
 * 选一个能覆盖最多片段的窗口：同一处引用的片段在文档里是挨着的，
 * 而重复文本（复述、解答区、目录）只会单独命中一两个片段。
 * 覆盖数相同时优先“更紧凑”的窗口（片段跨度更小），避免大窗口把两处内容一起圈进来。
 */
function bestWindow(hits: Array<SegmentHit | null>, width: number): { from: number; to: number } {
  const anchors = hits.flatMap((hit) => (hit ? hit.positions : []))
  if (!anchors.length) return { from: 0, to: Infinity }
  let bestCovered = -1
  let bestSpread = Infinity
  let bestFrom = anchors[0]
  for (const anchor of anchors) {
    const to = anchor + width
    let covered = 0
    let min = Infinity
    let max = -Infinity
    for (const hit of hits) {
      if (!hit) continue
      const inside = hit.positions.filter((p) => p >= anchor && p < to)
      if (!inside.length) continue
      covered++
      min = Math.min(min, inside[0])
      max = Math.max(max, inside[inside.length - 1])
    }
    if (!covered) continue
    const spread = max - min
    if (covered > bestCovered || (covered === bestCovered && spread < bestSpread)) {
      bestCovered = covered
      bestSpread = spread
      bestFrom = anchor
    }
  }
  return { from: bestFrom, to: bestFrom + width }
}

/** 把归一化下标区间包裹成 <mark>，返回包裹的节点数 */
function wrapRange(hay: Haystack, at: number, length: number, citation: number): number {
  const rawStart = hay.map[at]
  const rawEnd = hay.map[Math.min(at + length - 1, hay.map.length - 1)] + 1

  // 找出与 [rawStart, rawEnd) 相交的文本节点（从后往前包裹，避免偏移失效）
  const touched: Array<{ node: Text; from: number; to: number }> = []
  for (const span of hay.spans) {
    const from = Math.max(rawStart, span.start)
    const to = Math.min(rawEnd, span.end)
    if (from >= to) continue
    touched.push({ node: span.node, from: from - span.start, to: to - span.start })
  }
  let hits = 0
  for (let i = touched.length - 1; i >= 0; i--) {
    const { node, from, to } = touched[i]
    const max = node.nodeValue?.length ?? 0
    const start = Math.max(0, Math.min(from, max))
    const end = Math.max(start, Math.min(to, max))
    if (start >= end) continue
    const mark = document.createElement('mark')
    mark.className = MARK_CLASS
    mark.dataset.cite = String(citation)
    const range = document.createRange()
    range.setStart(node, start)
    range.setEnd(node, end)
    try {
      range.surroundContents(mark)
      hits++
    } catch {
      // 极少数跨元素边界的情况：跳过，不影响其他片段
    }
  }
  return hits
}

/** 在单个容器里按聚类窗口包裹所有片段，返回包裹的节点数 */
function applyInContainer(
  root: HTMLElement,
  plan: string[][],
  citation: number,
  width: number,
): number {
  if (!plan.length) return 0
  const hay = buildHaystack(root)
  if (!hay.normalized) return 0

  const hits = plan.map((needles) => locateSegment(hay.normalized, needles))
  if (!hits.some(Boolean)) return 0

  // 窗口随文档长度自适应：短文档不必用满 3000 字，否则等于全文
  const span = Math.min(width, Math.max(600, Math.round(hay.normalized.length / 6)))
  const window = bestWindow(hits, span)

  // 每个片段只取窗口内的第一处出现；窗口外的命中一律忽略（那些是重复文本）
  const ranges: Array<{ at: number; len: number }> = []
  for (const hit of hits) {
    if (!hit) continue
    const at = hit.positions.find((p) => p >= window.from && p < window.to)
    if (at === undefined) continue
    ranges.push({ at, len: hit.needle.length })
  }
  // 从后往前包裹：先动靠后的位置，前面文本节点的偏移不受影响
  ranges.sort((a, b) => b.at - a.at)

  let marks = 0
  let boundary = Infinity // 已包裹区间的最小起点，用于丢弃相互重叠的片段
  for (const range of ranges) {
    if (marks >= MAX_MARKS) break
    if (range.at + range.len > boundary) continue
    marks += wrapRange(hay, range.at, range.len, citation)
    boundary = range.at
  }
  return marks
}

export interface ApplyOptions {
  /** 分页容器的选择器（PDF 用 .viewer-page 把匹配限制在单页内） */
  pageSelector?: string
  /** 提示优先匹配的页码（1-based） */
  preferPages?: number[]
}

/** 应用一组引用高亮，返回真正命中的引用编号 */
export function applyCitationHighlights(
  root: HTMLElement,
  highlights: CitationHighlight[],
  options: ApplyOptions = {},
): number[] {
  const found: number[] = []
  const pages = options.pageSelector
    ? Array.from(root.querySelectorAll<HTMLElement>(options.pageSelector))
    : []

  for (const item of highlights) {
    const plan = citationSegments(item.text).map((segment) => [
      segment,
      ...clauseSegments(segment),
    ])
    if (!plan.length) continue

    let marks = 0
    if (pages.length) {
      // 分页文档（PDF）：按提示页优先逐页尝试，某一页命中后只在该页内包裹全部片段
      const hints = item.pages?.length ? item.pages : options.preferPages ?? []
      const priority: HTMLElement[] = []
      for (const p of hints) {
        for (const offset of [0, -1, 1]) {
          const page = pages[p - 1 + offset]
          if (page && !priority.includes(page)) priority.push(page)
        }
      }
      const rest = pages
        .filter((page) => !priority.includes(page))
        .slice(0, Math.max(0, 80 - priority.length))
      for (const container of [...priority, ...rest]) {
        marks = applyInContainer(container, plan, item.index, Infinity)
        if (marks > 0) break
      }
    } else {
      // 整篇一个容器（docx / pptx）：用聚类窗口锁定最贴合的那一处
      marks = applyInContainer(root, plan, item.index, CLUSTER_WINDOW)
    }

    if (marks > 0) found.push(item.index)
  }
  return found
}

/** 清除全部引用高亮（保留原有文案与结构） */
export function clearCitationHighlights(root: HTMLElement) {
  const marks = Array.from(root.querySelectorAll<HTMLElement>(`mark.${MARK_CLASS}`))
  const parents = new Set<Node | null>()
  for (const mark of marks) {
    const parent = mark.parentNode
    parents.add(parent)
    while (mark.firstChild) parent?.insertBefore(mark.firstChild, mark)
    parent?.removeChild(mark)
  }
  parents.forEach((p) => {
    if (p && p.nodeType === Node.ELEMENT_NODE) (p as Element).normalize()
  })
}
