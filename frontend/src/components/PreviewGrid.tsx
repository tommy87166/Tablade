/**
 * 預覽表格：列虛擬捲動，只向 Python 要畫面附近的資料（get_rows 分頁，DESIGN.md §16.4）。
 * 欄標題與儲存格的右鍵選單可以直接新增步驟（帶入點選的欄位或值）。
 */
import { useVirtualizer } from '@tanstack/react-virtual'
import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type Ref } from 'react'

import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import { tr } from '@/i18n'
import type { Cell, TabladeApi, ViewState } from '../bridge'

const PAGE = 200
const ROW_HEIGHT = 28
/** 量文字寬度用的隱藏 span（不佔版面、看不到）。 */
const MEASURE_BASE = 'pointer-events-none invisible absolute top-0 left-0 whitespace-pre'

export interface GridHandle {
  scrollToRow: (index: number) => void
}

export interface AddStepRequest {
  type: string
  context: { column?: string; value?: Cell; exclude?: boolean }
}

interface Props {
  api: TabladeApi
  preview: ViewState['preview']
  /** 內容改變時換一個值，表格會丟掉快取重新載入 */
  resetKey: string
  showSystem: boolean
  /** 最大的行號（所有來源檔的總行數），決定行號欄的寬度 */
  maxLine?: number | null
  onAddStep: (request: AddStepRequest) => void
  onLoaded?: (total: number) => void
  ref?: Ref<GridHandle>
}

/** 粗估文字寬度：中日韓字元約 14px，其他約 7.5px（14px 字型）。只用來挑最長的幾個值去實際量。 */
function textWidth(text: string): number {
  let width = 0
  for (const ch of text) width += ch.codePointAt(0)! >= 0x2e80 ? 14 : 7.5
  return width
}

const SAMPLE_ROWS = 200
/** 每一欄實際量幾個最長的值（用粗估挑出來）。 */
const MEASURE_CANDIDATES = 5
/** 儲存格左右留白（px-2）、左框線，再多留一點，避免字型捨入造成截斷。 */
const CELL_EXTRA = 16 + 1 + 7
const MIN_WIDTH = 64
const MAX_WIDTH = 360

const cellText = (v: Cell) => (v === null ? 'null' : String(v))

/** 使用者拖曳調過的欄寬（依欄名）：切換步驟、階段時同名欄維持；只記在這次開著的期間。 */
const manualWidths = new Map<string, number>()
const RESIZE_MIN = 40
const RESIZE_MAX = 1200

/** 欄標題右邊界的拖曳把手：拖曳調整欄寬，雙擊回到自動寬度。 */
function ResizeHandle({ name, width, onResize }: { name: string; width: number; onResize: () => void }) {
  const drag = useRef<{ x: number; width: number } | null>(null)
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={tr(`調整「${name}」的欄寬`, `Resize column "${name}"`)}
      title={tr('拖曳調整欄寬，雙擊回到自動寬度', 'Drag to resize; double-click to fit')}
      className="absolute top-0 right-0 h-full w-1.5 cursor-col-resize hover:bg-primary/40"
      onPointerDown={(e) => {
        e.preventDefault()
        e.currentTarget.setPointerCapture(e.pointerId)
        drag.current = { x: e.clientX, width }
      }}
      onPointerMove={(e) => {
        if (!drag.current) return
        const next = Math.round(drag.current.width + e.clientX - drag.current.x)
        manualWidths.set(name, Math.min(RESIZE_MAX, Math.max(RESIZE_MIN, next)))
        onResize()
      }}
      onPointerUp={() => {
        drag.current = null
      }}
      onDoubleClick={() => {
        manualWidths.delete(name)
        onResize()
      }}
    />
  )
}

function clampWidth(content: number): number {
  return Math.ceil(Math.min(Math.max(content + CELL_EXTRA, MIN_WIDTH), MAX_WIDTH))
}

/** 量不到實際寬度之前（第一次畫面）用的粗估寬度。 */
function estimateWidth(name: string, sample: Cell[]): number {
  return clampWidth(Math.max(textWidth(name) + 12, ...sample.map((v) => textWidth(cellText(v)))))
}

/** 最長（粗估）的幾個不重複的值，交給 DOM 實際量寬度。 */
function candidates(values: string[]): string[] {
  return [...new Set(values)].sort((a, b) => textWidth(b) - textWidth(a)).slice(0, MEASURE_CANDIDATES)
}

export function PreviewGrid({ api, preview, resetKey, showSystem, maxLine, onAddStep, onLoaded, ref }: Props) {
  const scroller = useRef<HTMLDivElement>(null)
  const [pages, setPages] = useState<Map<number, Cell[][]>>(new Map())
  const [columns, setColumns] = useState<string[]>([])
  const [target, setTarget] = useState<{ name: string; value: Cell } | null>(null)
  const loading = useRef(new Set<number>())
  const total = preview.rows ?? 0
  const step = preview.step

  // 換步驟或 recipe 改變時，丟掉快取的分頁
  useEffect(() => {
    setPages(new Map())
    setColumns([])
    loading.current = new Set()
    scroller.current?.scrollTo({ top: 0 })
  }, [step, resetKey])

  const load = useCallback(
    async (page: number) => {
      if (loading.current.has(page)) return
      loading.current.add(page)
      const res = await api.rows(step, page * PAGE, PAGE)
      if (!res.ok) return
      setColumns(res.columns)
      setPages((prev) => new Map(prev).set(page, res.rows))
      if (page === 0) onLoaded?.(res.total)
    },
    [api, step, onLoaded],
  )

  // TanStack Virtual 和 React Compiler 不相容的提示；這個專案沒有用 React Compiler
  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({
    count: total,
    getScrollElement: () => scroller.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 20,
  })
  const items = virtualizer.getVirtualItems()

  useEffect(() => {
    if (!total) return
    const first = items[0]?.index ?? 0
    const last = items[items.length - 1]?.index ?? 0
    for (let p = Math.floor(first / PAGE); p <= Math.floor(last / PAGE); p++) {
      if (!pages.has(p)) void load(p)
    }
    if (!items.length && !pages.has(0)) void load(0)
  }, [items, pages, total, load])

  useImperativeHandle(ref, () => ({ scrollToRow: (index: number) => virtualizer.scrollToIndex(index, { align: 'center' }) }))

  const visible = useMemo(() => {
    const idx = columns.map((name, i) => ({ name, i }))
    return idx.filter(({ name }) => showSystem || !name.startsWith('__'))
  }, [columns, showSystem])
  const lineIndex = columns.indexOf('__line_no')
  const fileIndex = columns.indexOf('__source_file')
  const dtypes = new Map(preview.columns.map((c) => [c.name, c.dtype]))

  const firstPage = pages.get(0)
  const estimated = useMemo(() => {
    const sample = (firstPage ?? []).slice(0, SAMPLE_ROWS)
    return visible.map(({ name, i }) =>
      estimateWidth(
        name,
        sample.map((r) => r[i]),
      ),
    )
  }, [visible, firstPage])
  // 行號欄依最大行號的位數加寬（大檔的行號可能有 8、9 位數）
  const maxLineText = String(Math.max(maxLine ?? 0, preview.rows ?? 0, 1))

  // 欄寬用畫面上實際的字型量（含 tabular-nums；App 的系統字型比估計的寬，數字會被截斷）。
  // 量的 span 放在表格裡，字型、字級、數字樣式都跟儲存格一樣。
  const measurer = useRef<HTMLSpanElement>(null)
  const [measured, setMeasured] = useState<{ widths: number[]; line: number } | null>(null)
  useLayoutEffect(() => {
    const span = measurer.current
    if (!span) return
    const width = (text: string, className: string) => {
      span.className = `${MEASURE_BASE} ${className}`
      span.textContent = text
      return span.getBoundingClientRect().width
    }
    const sample = (firstPage ?? []).slice(0, SAMPLE_ROWS)
    const widths = visible.map(({ name, i }) => {
      const values = candidates(sample.map((r) => cellText(r[i])))
      return clampWidth(Math.max(width(name, 'font-medium'), ...values.map((v) => width(v, ''))))
    })
    const line = Math.ceil(Math.max(56, width(maxLineText, 'text-xs') + CELL_EXTRA))
    span.textContent = ''
    setMeasured({ widths, line })
  }, [visible, firstPage, maxLineText])
  const [, setResized] = useState(0)
  const resized = useCallback(() => setResized((n) => n + 1), [])
  const auto = measured && measured.widths.length === visible.length ? measured.widths : estimated
  const widths = visible.map(({ name }, k) => manualWidths.get(name) ?? auto[k])
  const lineWidth = measured?.line ?? Math.max(56, maxLineText.length * 7.5 + 22)
  const template = `${lineWidth}px ${widths.map((w) => `${w}px`).join(' ')}`

  if (preview.rows === null) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {tr('沒有可預覽的資料', 'No data to preview')}
      </div>
    )
  }

  return (
    <div ref={scroller} className="relative h-full overflow-auto text-sm tabular-nums">
      <span ref={measurer} aria-hidden className={MEASURE_BASE} />
      <div style={{ width: 'max-content', minWidth: '100%' }}>
        <div
          className="sticky top-0 z-10 grid border-b bg-background font-medium"
          style={{ gridTemplateColumns: template, height: ROW_HEIGHT + 8 }}
        >
          <div className="px-2 py-1.5 text-right text-xs text-muted-foreground">{tr('行號', 'Line')}</div>
          {visible.map(({ name }, k) => (
            <div
              key={name}
              className="relative truncate border-l px-2 py-1.5"
              title={tr(`${name}（${dtypes.get(name) ?? ''}）`, `${name} (${dtypes.get(name) ?? ''})`)}
            >
              {name}
              <ResizeHandle name={name} width={widths[k]} onResize={resized} />
            </div>
          ))}
        </div>

        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div
              style={{ height: virtualizer.getTotalSize(), position: 'relative' }}
              onContextMenu={(e) => {
                const cell = (e.target as HTMLElement).closest<HTMLElement>('[data-col]')
                const name = cell?.dataset.col
                const rowIndex = Number(cell?.dataset.row)
                const row = pages.get(Math.floor(rowIndex / PAGE))?.[rowIndex % PAGE]
                const i = name ? columns.indexOf(name) : -1
                setTarget(name && row && i >= 0 && !name.startsWith('__') ? { name, value: row[i] } : null)
              }}
            >
              {items.map((item) => {
                const row = pages.get(Math.floor(item.index / PAGE))?.[item.index % PAGE]
                return (
                  <div
                    key={item.key}
                    className="absolute left-0 grid border-b even:bg-muted/40"
                    style={{ gridTemplateColumns: template, height: ROW_HEIGHT, transform: `translateY(${item.start}px)`, width: '100%' }}
                  >
                    <div
                      className="truncate px-2 py-1 text-right text-xs text-muted-foreground"
                      title={row && fileIndex >= 0 ? String(row[fileIndex]) : undefined}
                    >
                      {row && lineIndex >= 0 ? String(row[lineIndex]) : item.index + 1}
                    </div>
                    {visible.map(({ name, i }) => {
                      if (!row)
                        return (
                          <div key={name} className="border-l px-2 py-1 text-muted-foreground/40">
                            …
                          </div>
                        )
                      const value = row[i]
                      return (
                        <div
                          key={name}
                          data-col={name}
                          data-row={item.index}
                          className={`truncate border-l px-2 py-1 ${value === null ? 'italic text-muted-foreground' : ''}`}
                          title={value === null ? undefined : String(value)}
                        >
                          {value === null ? 'null' : String(value)}
                        </div>
                      )
                    })}
                  </div>
                )
              })}
            </div>
          </ContextMenuTrigger>
          {target ? (
            <ContextMenuContent>
              <ContextMenuLabel className="max-w-64 truncate">
                {target.name}
                {tr('：', ': ')}
                {target.value === null ? 'null' : String(target.value)}
              </ContextMenuLabel>
              <ContextMenuSeparator />
              <ContextMenuItem onSelect={() => onAddStep({ type: 'filter', context: { column: target.name, value: target.value } })}>
                {tr('只保留等於這個值的列', 'Keep only rows equal to this value')}
              </ContextMenuItem>
              <ContextMenuItem
                onSelect={() => onAddStep({ type: 'filter', context: { column: target.name, value: target.value, exclude: true } })}
              >
                {tr('排除等於這個值的列', 'Exclude rows equal to this value')}
              </ContextMenuItem>
              <ContextMenuSeparator />
              <ContextMenuItem
                onSelect={() => onAddStep({ type: 'replace_values', context: { column: target.name, value: target.value } })}
              >
                {tr('取代這個值', 'Replace this value')}
              </ContextMenuItem>
            </ContextMenuContent>
          ) : null}
        </ContextMenu>
      </div>
    </div>
  )
}
