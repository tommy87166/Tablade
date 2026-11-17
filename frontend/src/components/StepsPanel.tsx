/**
 * 左側：目前階段的讀取資料 → 步驟清單（點選、拖曳排序、停用、刪除）→ 輸出資料（中間的階段是「階段結果」），以及新增步驟選單。
 * 標題列可以切換精簡／詳細（預設精簡）：精簡只顯示步驟名稱與錯誤、警告的標記；詳細另外顯示步驟說明、列數與來源檔數。
 */
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CircleX,
  FileInput,
  FileOutput,
  GripVertical,
  List,
  ListCollapse,
  Pause,
  Play,
  Plus,
  Trash2,
} from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuTrigger } from '@/components/ui/context-menu'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { CatalogItem, ViewState } from '../bridge'
import { CollapseButton, PaneHeader, PaneTitle } from './Pane'
import { StepScope } from './StepScope'

interface Props {
  state: ViewState
  catalog: CatalogItem[]
  busy: boolean
  onSelect: (index: number) => void
  onAdd: (type: string) => void
  onMove: (index: number, to: number) => void
  onToggle: (index: number) => void
  onDelete: (index: number) => void
  onCollapse: () => void
}

function rowsText(rows: number | null) {
  return rows === null ? null : `${rows.toLocaleString()} 列`
}

/** 新增步驟選單裡，每一欄最多幾個步驟；多的往右邊排一欄。 */
const PER_COLUMN = 5

export function AddStepMenu({ catalog, onAdd, disabled }: { catalog: CatalogItem[]; onAdd: (type: string) => void; disabled?: boolean }) {
  const groups = new Map<string, CatalogItem[]>()
  for (const item of catalog) groups.set(item.category, [...(groups.get(item.category) ?? []), item])
  const categories = [...groups.keys()]
  // 記得上次看的分類（選單關掉再打開還是同一個分頁）
  const [tab, setTab] = useState(categories[0] ?? '')
  const current = groups.has(tab) ? tab : (categories[0] ?? '')
  const items = groups.get(current) ?? []
  const columns = Math.max(1, Math.ceil(items.length / PER_COLUMN))
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="xs" disabled={disabled}>
          <Plus /> 新增步驟
        </Button>
      </DropdownMenuTrigger>
      {/* 寬度依欄數決定（內容可以左右捲動時，瀏覽器不會照內容撐開選單）；太寬時左右捲動 */}
      <DropdownMenuContent
        className="max-w-[calc(100vw-2rem)] p-0"
        style={{ width: `calc(${columns} * 16rem + ${columns - 1} * 0.75rem + 0.75rem)` }}
        align="start"
      >
        {/* 分類用分頁切換 */}
        <div role="tablist" aria-label="步驟分類" className="flex gap-1 border-b p-1.5">
          {categories.map((category) => (
            <button
              key={category}
              type="button"
              role="tab"
              aria-selected={category === current}
              onClick={() => setTab(category)}
              className={`rounded-md px-3 py-1 text-sm font-medium ${
                category === current ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
              }`}
            >
              {category}
              <span className={`ml-1.5 text-xs ${category === current ? 'opacity-80' : 'text-muted-foreground/70'}`}>
                {groups.get(category)?.length}
              </span>
            </button>
          ))}
        </div>
        {/* 由上往下排，每一欄最多 PER_COLUMN 個，多的往右邊排；同一列等高，內容靠上 */}
        <div
          role="tabpanel"
          className="grid grid-flow-col gap-x-3 overflow-x-auto p-1.5"
          style={{
            gridTemplateRows: `repeat(${Math.min(PER_COLUMN, Math.max(items.length, 1))}, auto)`,
            gridTemplateColumns: `repeat(${columns}, 16rem)`,
          }}
        >
          {items.map((item) => (
            <DropdownMenuItem
              key={item.type}
              onSelect={() => onAdd(item.type)}
              className="my-1 items-start rounded-l-none border-l-2 border-muted-foreground/50 pl-2.5 focus:border-primary"
            >
              <div>
                <div>{item.label}</div>
                <StepScope scope={item.scope} />
                <div className="text-xs text-muted-foreground">{item.description}</div>
              </div>
            </DropdownMenuItem>
          ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function StepsPanel({ state, catalog, busy, onSelect, onAdd, onMove, onToggle, onDelete, onCollapse }: Props) {
  const [dragging, setDragging] = useState<number | null>(null)
  const [over, setOver] = useState<number | null>(null)
  /** 詳細：顯示步驟說明、列數與來源檔數；預設精簡 */
  const [detailed, setDetailed] = useState(false)
  const input = state.input
  const missing = input.entries.filter((e) => !e.found).length
  const sourceText =
    input.reader === 'stages'
      ? input.stages.length
        ? `讀 ${input.stages.map((n) => `@${n}`).join('、')}${input.error ? '・有錯誤' : input.loaded ? '' : '・還沒有結果'}`
        : '尚未選擇前面的階段'
      : !input.files.length
        ? '尚未加入檔案'
        : missing
          ? `有 ${missing} 個找不到的檔`
          : input.loaded
            ? `${input.sources.length} 個檔案・已載入`
            : input.stale
              ? '設定已變更・請重新載入'
              : `${input.files.length} 個項目・尚未載入`
  const sourceTone =
    missing || (input.reader === 'stages' && input.error)
      ? 'text-destructive'
      : input.stale
        ? 'text-amber-700 dark:text-amber-400'
        : 'text-muted-foreground'
  const last = state.steps.length - 1
  const stage = state.stages[state.current]

  return (
    <div className="flex h-full flex-col">
      <PaneHeader>
        <PaneTitle>
          <span className="block max-w-40 truncate" title={stage?.name}>
            {state.stages.length > 1 && stage ? stage.name : '步驟'}
          </span>
        </PaneTitle>
        {detailed && state.input.sampled ? (
          <span className="truncate text-[11px] text-amber-700 dark:text-amber-400">列數為樣本</span>
        ) : null}
        <span className="ml-auto flex shrink-0 items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-pressed={detailed}
                aria-label={detailed ? '改成精簡' : '改成詳細'}
                onClick={() => setDetailed((v) => !v)}
              >
                {detailed ? <ListCollapse /> : <List />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{detailed ? '改成精簡（只顯示步驟名稱）' : '改成詳細（顯示步驟說明與列數）'}</TooltipContent>
          </Tooltip>
          <AddStepMenu catalog={catalog} onAdd={onAdd} disabled={busy} />
          <CollapseButton side="left" label="步驟" onClick={onCollapse} />
        </span>
      </PaneHeader>
      <ol className="flex-1 space-y-1 overflow-auto p-2">
        <li>
          <button
            type="button"
            onClick={() => onSelect(-1)}
            className={`flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted ${state.selected === -1 ? 'bg-primary/10 ring-1 ring-primary/40' : ''}`}
          >
            <FileInput className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="block font-medium">讀取資料</span>
              {/* 精簡時只在有問題（找不到檔、要重新載入、讀取錯誤）時顯示 */}
              {detailed || sourceTone !== 'text-muted-foreground' ? (
                <span className={`block truncate text-xs ${sourceTone}`}>{sourceText}</span>
              ) : null}
            </span>
            {detailed && input.loaded && input.total_rows !== null ? (
              <span className="text-xs text-muted-foreground tabular-nums">{input.total_rows.toLocaleString()} 行</span>
            ) : null}
          </button>
        </li>
        {state.steps.map((step) => {
          const selected = state.selected === step.index
          const rows = rowsText(step.rows)
          return (
            <ContextMenu key={step.id + step.index}>
              <ContextMenuTrigger asChild>
                <li
                  draggable
                  onDragStart={(e) => {
                    setDragging(step.index)
                    e.dataTransfer.effectAllowed = 'move'
                  }}
                  onDragOver={(e) => {
                    e.preventDefault()
                    setOver(step.index)
                  }}
                  onDragLeave={() => setOver(null)}
                  onDrop={(e) => {
                    e.preventDefault()
                    if (dragging !== null && dragging !== step.index) onMove(dragging, step.index)
                    setDragging(null)
                    setOver(null)
                  }}
                  onDragEnd={() => {
                    setDragging(null)
                    setOver(null)
                  }}
                  className={`group rounded-md ${over === step.index && dragging !== null ? 'ring-2 ring-primary/60' : ''}`}
                >
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => onSelect(step.index)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') onSelect(step.index)
                      if (e.altKey && e.key === 'ArrowUp' && step.index > 0) onMove(step.index, step.index - 1)
                      if (e.altKey && e.key === 'ArrowDown' && step.index < last) onMove(step.index, step.index + 1)
                    }}
                    className={`flex cursor-pointer items-start gap-1.5 rounded-md px-1.5 py-1.5 text-sm hover:bg-muted ${selected ? 'bg-primary/10 ring-1 ring-primary/40' : ''} ${step.enabled ? '' : 'opacity-50'}`}
                  >
                    <GripVertical className="mt-0.5 size-4 shrink-0 cursor-grab text-muted-foreground/50" />
                    <span className="mt-0.5 w-5 shrink-0 text-right text-xs text-muted-foreground tabular-nums">{step.index + 1}</span>
                    <span className="min-w-0 flex-1">
                      {detailed ? null : (
                        <span className="flex min-w-0 items-center gap-1">
                          <span className={`min-w-0 truncate font-medium ${step.enabled ? '' : 'line-through'}`}>{step.label}</span>
                          {step.status === 'error' ? (
                            <Badge variant="destructive" className="h-4 shrink-0 px-1 text-[10px]">
                              <CircleX /> 錯誤
                            </Badge>
                          ) : null}
                          {step.warning_count ? (
                            <Badge
                              variant="outline"
                              className="h-4 shrink-0 border-amber-500/50 px-1 text-[10px] text-amber-700 dark:text-amber-400"
                            >
                              <AlertTriangle /> {step.warning_count.toLocaleString()}
                            </Badge>
                          ) : null}
                        </span>
                      )}
                      {detailed ? <span className={`block font-medium ${step.enabled ? '' : 'line-through'}`}>{step.label}</span> : null}
                      {detailed ? (
                        <span className="block truncate text-xs text-muted-foreground">
                          {step.status === 'error' ? (step.run_error ?? step.errors[0]?.error ?? '參數有誤') : (step.describe ?? '')}
                        </span>
                      ) : null}
                      {detailed ? (
                        <span className="mt-0.5 flex flex-wrap items-center gap-1">
                          {rows ? <span className="text-xs whitespace-nowrap text-muted-foreground tabular-nums">{rows}</span> : null}
                          {step.files !== null ? (
                            <span className="text-xs whitespace-nowrap text-muted-foreground tabular-nums">{step.files} 個來源檔</span>
                          ) : null}
                          {step.status === 'error' ? (
                            <Badge variant="destructive" className="h-4 px-1 text-[10px]">
                              <CircleX /> 錯誤
                            </Badge>
                          ) : null}
                          {step.status === 'pending' ? (
                            <span className="text-xs text-muted-foreground">（前面的步驟有錯，尚未計算）</span>
                          ) : null}
                          {step.warning_count ? (
                            <Badge
                              variant="outline"
                              className="h-4 border-amber-500/50 px-1 text-[10px] text-amber-700 dark:text-amber-400"
                            >
                              <AlertTriangle /> {step.warning_count.toLocaleString()}
                            </Badge>
                          ) : null}
                        </span>
                      ) : null}
                    </span>
                    <span className="flex shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={step.enabled ? '停用' : '啟用'}
                        onClick={(e) => {
                          e.stopPropagation()
                          onToggle(step.index)
                        }}
                      >
                        {step.enabled ? <Pause /> : <Play />}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label="刪除"
                        onClick={(e) => {
                          e.stopPropagation()
                          onDelete(step.index)
                        }}
                      >
                        <Trash2 />
                      </Button>
                    </span>
                  </div>
                </li>
              </ContextMenuTrigger>
              <ContextMenuContent>
                <ContextMenuItem disabled={step.index === 0} onSelect={() => onMove(step.index, step.index - 1)}>
                  <ArrowUp /> 上移
                </ContextMenuItem>
                <ContextMenuItem disabled={step.index === last} onSelect={() => onMove(step.index, step.index + 1)}>
                  <ArrowDown /> 下移
                </ContextMenuItem>
                <ContextMenuItem onSelect={() => onToggle(step.index)}>
                  {step.enabled ? <Pause /> : <Play />} {step.enabled ? '停用' : '啟用'}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem variant="destructive" onSelect={() => onDelete(step.index)}>
                  <Trash2 /> 刪除
                </ContextMenuItem>
              </ContextMenuContent>
            </ContextMenu>
          )
        })}
        <li>
          <button
            type="button"
            onClick={() => onSelect(state.steps.length)}
            className={`flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted ${state.selected === state.steps.length ? 'bg-primary/10 ring-1 ring-primary/40' : ''}`}
          >
            <FileOutput className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="block font-medium">{stage && !stage.last ? '階段結果' : '輸出資料'}</span>
              {detailed && stage && !stage.last ? (
                <span className="block text-xs text-muted-foreground">暫存，後面的階段用 @{stage.name} 讀它</span>
              ) : null}
            </span>
          </button>
        </li>
      </ol>
    </div>
  )
}
