/**
 * 預覽表格：列虛擬捲動，只向 Python 要畫面附近的資料（rows() 分頁，DESIGN.md §16.4）。
 * 儲存格的右鍵選單可以直接新增步驟（帶入點選的欄位與值）；欄標題沒有右鍵選單。
 *
 * - 換步驟或內容變了（resetKey）時丟掉快取，世代編號加一：還在路上的回應回來時編號不同就丟掉。
 *   回應的 key（資料的指紋）和畫面狀態的 preview.key 不同時也丟掉（Python 已經換了內容，新的畫面狀態隨後就到）。
 * - 取資料失敗的那一頁不自動重試（避免一直失敗一直要），表格上面寫原因、可以按「重試」。
 * - 列數太多時分段顯示（瀏覽器對單一元素的高度有上限）：一段最多 SEGMENT_ROWS 列，上面切換是哪一段。
 * - 點一格（滑鼠左鍵）就選到那一格、加外框，完整的內容交給下方面板的「檢視」分頁（onSelectCell）；
 *   儲存格沒有滑鼠停留的提示（docs/cell-view-plan.md）。換步驟、內容變了就清掉。
 * - 點警告跳過來的那一列整列標成警告的顏色（scrollToRow），直到跳到別列、換步驟或內容變了。
 */
import { useVirtualizer } from '@tanstack/react-virtual'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type Ref } from 'react'

import { Button } from '@/components/ui/button'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { plural, tr } from '@/i18n'
import { cellView, type CellView } from '@/lib/cell'
import type { Cell, TabladeApi, ViewState } from '../bridge'

const PAGE = 200
const ROW_HEIGHT = 28
/**
 * 一段最多幾列：瀏覽器對單一元素的高度有上限（Chromium、WebKit 約 3,355 萬 px，Firefox 約 1,789 萬 px），
 * 虛擬捲動的高度是「列數 × 列高」，超過就捲不到後面。50 萬 × 28px = 1,400 萬 px，三種都在上限內。
 */
const SEGMENT_ROWS = 500_000
/** 量文字寬度用的隱藏 span（不佔版面、看不到）。 */
const MEASURE_BASE = 'pointer-events-none invisible absolute top-0 left-0 whitespace-pre'

export interface GridHandle {
  /** 捲到那一列（整個結果的第幾列，從 0 起）並把整列標起來（點警告跳過來的） */
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
  /** 點了一格（交給「檢視」分頁）；清掉選到的格子時是 null */
  onSelectCell?: (cell: CellView | null) => void
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

export function PreviewGrid({ api, preview, resetKey, showSystem, maxLine, onAddStep, onLoaded, onSelectCell, ref }: Props) {
  const scroller = useRef<HTMLDivElement>(null)
  const [pages, setPages] = useState<Map<number, Cell[][]>>(new Map())
  const [loadedColumns, setColumns] = useState<string[]>([])
  const [target, setTarget] = useState<{ name: string; value: Cell } | null>(null)
  /** 選到的格子：整個結果的第幾列（從 0 起）與欄名 */
  const [selected, setSelected] = useState<{ row: number; name: string } | null>(null)
  /** 點警告跳過來的列（整個結果的第幾列，從 0 起） */
  const [marked, setMarked] = useState<number | null>(null)
  const selectCell = useRef(onSelectCell)
  useEffect(() => {
    selectCell.current = onSelectCell
  })
  const loading = useRef(new Set<number>())
  /** 取不到資料的頁（不自動重試）與原因 */
  const failed = useRef(new Set<number>())
  const [loadError, setLoadError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  /** 換步驟或內容變了就加一：之前送出的請求回來時編號不同，丟掉 */
  const generation = useRef(0)
  /** 畫面狀態裡預覽內容的指紋（和回應的 key 比） */
  const viewKey = useRef(preview.key)
  useEffect(() => {
    viewKey.current = preview.key
  })
  const [segment, setSegment] = useState(0)
  /** 切到另一段之後要捲到的列（那一段裡的第幾列） */
  const pendingScroll = useRef<number | null>(null)
  const total = preview.rows ?? 0
  const step = preview.step
  const segments = Math.max(1, Math.ceil(total / SEGMENT_ROWS))
  const seg = Math.min(segment, segments - 1)
  /** 這一段第一列是整個結果的第幾列（從 0 起） */
  const base = seg * SEGMENT_ROWS
  const count = Math.max(0, Math.min(SEGMENT_ROWS, total - base))
  // 0 列時不會向 Python 要資料：欄名用畫面狀態裡的（標題寫幾欄，表格就有幾欄）
  const stateColumns = preview.columns
  const columns = useMemo(() => (loadedColumns.length ? loadedColumns : stateColumns.map((c) => c.name)), [loadedColumns, stateColumns])

  // 換步驟或 recipe 改變時，丟掉快取的分頁
  useEffect(() => {
    generation.current += 1
    setPages(new Map())
    setColumns([])
    loading.current = new Set()
    failed.current = new Set()
    setLoadError(null)
    setSegment(0)
    pendingScroll.current = null
    scroller.current?.scrollTo({ top: 0 })
    setSelected(null)
    setMarked(null)
    selectCell.current?.(null)
  }, [step, resetKey])

  // 系統欄位藏起來時，選到的正好是系統欄位：清掉
  useEffect(() => {
    if (showSystem || !selected?.name.startsWith('__')) return
    setSelected(null)
    selectCell.current?.(null)
  }, [showSystem, selected])

  // 表格不畫了（沒有資料、那一步算不出來）：「檢視」分頁不要留著上一次的內容
  useEffect(() => () => selectCell.current?.(null), [])

  const load = useCallback(
    async (page: number) => {
      if (loading.current.has(page) || failed.current.has(page)) return
      const gen = generation.current
      const pending = loading.current
      pending.add(page)
      let res: Awaited<ReturnType<TabladeApi['rows']>>
      try {
        res = await api.rows(step, page * PAGE, PAGE)
      } catch (err) {
        res = { ok: false, error: String(err) }
      }
      if (gen !== generation.current) return // 換了步驟或內容：這是舊的資料
      // Python 那邊的內容已經變了（新的畫面狀態還在路上）：不用這份，也不重送（新的狀態到了會重置再要）
      if (res.ok && res.key !== null && viewKey.current !== null && res.key !== viewKey.current) return
      pending.delete(page)
      if (!res.ok) {
        failed.current.add(page)
        setLoadError(res.error)
        return
      }
      setColumns(res.columns)
      setPages((prev) => new Map(prev).set(page, res.rows))
      if (page === 0) onLoaded?.(res.total)
    },
    [api, step, onLoaded],
  )

  // TanStack Virtual 和 React Compiler 不相容的提示；這個專案沒有用 React Compiler
  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({ count, getScrollElement: () => scroller.current, estimateSize: () => ROW_HEIGHT, overscan: 20 })
  const items = virtualizer.getVirtualItems()

  useEffect(() => {
    if (!total) return
    const first = base + (items[0]?.index ?? 0)
    const last = base + (items[items.length - 1]?.index ?? 0)
    for (let p = Math.floor(first / PAGE); p <= Math.floor(last / PAGE); p++) {
      if (!pages.has(p)) void load(p)
    }
    if (!items.length && !pages.has(Math.floor(base / PAGE))) void load(Math.floor(base / PAGE))
  }, [items, pages, total, base, load, retry])

  // 換了一段：捲到要找的那一列（點警告跳過來的），否則回到這一段的最上面
  useEffect(() => {
    if (pendingScroll.current !== null) {
      virtualizer.scrollToIndex(pendingScroll.current, { align: 'center' })
      pendingScroll.current = null
    } else {
      scroller.current?.scrollTo({ top: 0 })
    }
  }, [seg, virtualizer])

  useImperativeHandle(ref, () => ({
    scrollToRow: (index: number) => {
      setMarked(index)
      const to = Math.min(Math.floor(index / SEGMENT_ROWS), segments - 1)
      if (to === seg) {
        virtualizer.scrollToIndex(index - base, { align: 'center' })
      } else {
        pendingScroll.current = index - to * SEGMENT_ROWS
        setSegment(to)
      }
    },
  }))

  const visible = useMemo(() => {
    const idx = columns.map((name, i) => ({ name, i }))
    return idx.filter(({ name }) => showSystem || !name.startsWith('__'))
  }, [columns, showSystem])
  const lineIndex = columns.indexOf('__line_no')
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

  const segmentText = (k: number) => {
    const from = (k * SEGMENT_ROWS + 1).toLocaleString()
    const to = Math.min((k + 1) * SEGMENT_ROWS, total).toLocaleString()
    return tr(`第 ${from}–${to} 列`, `Rows ${from}–${to}`)
  }

  return (
    <div className="flex h-full flex-col">
      {segments > 1 ? (
        <div className="flex shrink-0 items-center gap-1.5 border-b px-2 py-1 text-xs text-muted-foreground">
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={tr('上一段', 'Previous rows')}
            disabled={seg === 0}
            onClick={() => setSegment(seg - 1)}
          >
            <ChevronLeft />
          </Button>
          <Select value={String(seg)} onValueChange={(v) => setSegment(Number(v))}>
            <SelectTrigger size="sm" className="h-6 w-auto text-xs tabular-nums" aria-label={tr('顯示哪一段', 'Rows shown')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Array.from({ length: segments }, (_, k) => (
                <SelectItem key={k} value={String(k)} className="text-xs tabular-nums">
                  {segmentText(k)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={tr('下一段', 'Next rows')}
            disabled={seg === segments - 1}
            onClick={() => setSegment(seg + 1)}
          >
            <ChevronRight />
          </Button>
          <span className="tabular-nums">
            {tr(
              `共 ${total.toLocaleString()} 列，每段 ${SEGMENT_ROWS.toLocaleString()} 列`,
              `${plural(total, 'row', 'rows')} in total, ${SEGMENT_ROWS.toLocaleString()} per page`,
            )}
          </span>
        </div>
      ) : null}
      {loadError ? (
        <div className="flex shrink-0 items-center gap-2 border-b bg-destructive/10 px-3 py-1 text-xs text-destructive">
          <span className="min-w-0 flex-1 truncate" title={loadError}>
            {tr(`有些列讀不到：${loadError}`, `Some rows couldn't be read: ${loadError}`)}
          </span>
          <Button
            variant="outline"
            size="xs"
            onClick={() => {
              failed.current = new Set()
              setLoadError(null)
              setRetry((n) => n + 1)
            }}
          >
            {tr('重試', 'Retry')}
          </Button>
        </div>
      ) : null}
      <div ref={scroller} className="relative min-h-0 flex-1 overflow-auto text-sm tabular-nums">
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
                onClick={(e) => {
                  if (e.button !== 0) return
                  const cell = (e.target as HTMLElement).closest<HTMLElement>('[data-col]')
                  const name = cell?.dataset.col
                  const rowIndex = Number(cell?.dataset.row) // 整個結果的第幾列（不是這一段的）
                  const row = pages.get(Math.floor(rowIndex / PAGE))?.[rowIndex % PAGE]
                  if (!name || !row) return
                  const view = cellView(columns, row, rowIndex, name, dtypes.get(name) ?? null)
                  if (!view) return
                  setSelected({ row: rowIndex, name })
                  onSelectCell?.(view)
                }}
                onContextMenu={(e) => {
                  const cell = (e.target as HTMLElement).closest<HTMLElement>('[data-col]')
                  const name = cell?.dataset.col
                  const rowIndex = Number(cell?.dataset.row) // 整個結果的第幾列（不是這一段的）
                  const row = pages.get(Math.floor(rowIndex / PAGE))?.[rowIndex % PAGE]
                  const i = name ? columns.indexOf(name) : -1
                  setTarget(name && row && i >= 0 && !name.startsWith('__') ? { name, value: row[i] } : null)
                }}
              >
                {items.map((item) => {
                  const index = base + item.index
                  const row = pages.get(Math.floor(index / PAGE))?.[index % PAGE]
                  const warned = marked === index
                  return (
                    <div
                      key={item.key}
                      data-marked={warned || undefined}
                      className={`absolute left-0 grid border-b ${
                        warned ? 'bg-amber-200/80 shadow-[inset_3px_0_0_var(--color-amber-500)] dark:bg-amber-500/30' : 'even:bg-muted/40'
                      }`}
                      style={{ gridTemplateColumns: template, height: ROW_HEIGHT, transform: `translateY(${item.start}px)`, width: '100%' }}
                    >
                      <div className="truncate px-2 py-1 text-right text-xs text-muted-foreground">
                        {row && lineIndex >= 0 ? String(row[lineIndex]) : index + 1}
                      </div>
                      {visible.map(({ name, i }) => {
                        if (!row)
                          return (
                            <div key={name} className="border-l px-2 py-1 text-muted-foreground/40">
                              …
                            </div>
                          )
                        const value = row[i]
                        const picked = selected?.row === index && selected.name === name
                        return (
                          <div
                            key={name}
                            data-col={name}
                            data-row={index}
                            className={`truncate border-l px-2 py-1 ${value === null ? 'italic text-muted-foreground' : ''} ${
                              picked ? 'ring-2 ring-primary ring-inset' : ''
                            }`}
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
    </div>
  )
}
