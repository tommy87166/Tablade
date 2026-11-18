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
import { plural, tr } from '@/i18n'
import type { CatalogItem, StepState, ViewState } from '../bridge'
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
  return rows === null ? null : tr(`${rows.toLocaleString()} 列`, plural(rows, 'row', 'rows'))
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
          <Plus /> {tr('新增步驟', 'Add step')}
        </Button>
      </DropdownMenuTrigger>
      {/* 寬度依欄數決定（內容可以左右捲動時，瀏覽器不會照內容撐開選單）；太寬時左右捲動 */}
      <DropdownMenuContent
        className="max-w-[calc(100vw-2rem)] p-0"
        style={{ width: `calc(${columns} * 16rem + ${columns - 1} * 0.75rem + 0.75rem)` }}
        align="start"
      >
        {/* 分類用分頁切換 */}
        <div role="tablist" aria-label={tr('步驟分類', 'Step categories')} className="flex gap-1 border-b p-1.5">
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

/** 算不出來（pending）的步驟為什麼算不出來：前面哪一步有錯，或哪個前面的階段還沒有結果。 */
function blockedHint(step: StepState) {
  if (step.status !== 'pending') return null
  if (step.blocked_by !== null)
    return tr(
      `前面第 ${step.blocked_by + 1} 步有錯，這一步算不出來`,
      `Step ${step.blocked_by + 1} has an error, so this step can't be computed`,
    )
  if (step.blocked_stage)
    return tr(
      `@${step.blocked_stage} 還沒有結果，這一步算不出來`,
      `@${step.blocked_stage} has no result yet, so this step can't be computed`,
    )
  return tr('前面的步驟算不出來，這一步也算不出來', "An earlier step can't be computed, so neither can this one")
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
        ? tr(
            `讀 ${input.stages.map((n) => `@${n}`).join('、')}${input.error ? '・有錯誤' : input.loaded ? '' : '・還沒有結果'}`,
            `Reads ${input.stages.map((n) => `@${n}`).join(', ')}${input.error ? ' · has errors' : input.loaded ? '' : ' · no result yet'}`,
          )
        : tr('還沒選前面的階段', 'No earlier stage selected')
      : !input.files.length
        ? tr('還沒加入檔案', 'No files added')
        : missing
          ? tr(`有 ${missing} 個找不到的檔`, `${plural(missing, 'file', 'files')} not found`)
          : input.loaded
            ? tr(`${input.sources.length} 個檔案・已載入`, `${plural(input.sources.length, 'file', 'files')} · loaded`)
            : input.stale
              ? tr('設定已變更・要重新載入', 'Settings changed · reload needed')
              : tr(`${input.files.length} 個項目・還沒載入`, `${plural(input.files.length, 'item', 'items')} · not loaded`)
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
            {state.stages.length > 1 && stage ? stage.name : tr('步驟', 'Steps')}
          </span>
        </PaneTitle>
        {detailed && state.input.sampled ? (
          <span className="truncate text-[11px] text-amber-700 dark:text-amber-400">{tr('列數為樣本', 'Row counts from sample')}</span>
        ) : null}
        <span className="ml-auto flex shrink-0 items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-pressed={detailed}
                aria-label={detailed ? tr('改成精簡', 'Switch to compact') : tr('改成詳細', 'Switch to detailed')}
                onClick={() => setDetailed((v) => !v)}
              >
                {detailed ? <ListCollapse /> : <List />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {detailed
                ? tr('改成精簡（只顯示步驟名稱）', 'Switch to compact (step names only)')
                : tr('改成詳細（顯示步驟說明與列數）', 'Switch to detailed (show step descriptions and row counts)')}
            </TooltipContent>
          </Tooltip>
          <AddStepMenu catalog={catalog} onAdd={onAdd} disabled={busy} />
          <CollapseButton side="left" label={tr('步驟', 'Steps')} onClick={onCollapse} />
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
              <span className="block font-medium">{tr('讀取資料', 'Read data')}</span>
              {/* 精簡時只在有問題（找不到檔、要重新載入、讀取錯誤）時顯示 */}
              {detailed || sourceTone !== 'text-muted-foreground' ? (
                <span className={`block truncate text-xs ${sourceTone}`}>{sourceText}</span>
              ) : null}
            </span>
            {detailed && input.loaded && input.total_rows !== null ? (
              <span className="text-xs text-muted-foreground tabular-nums">
                {tr(`${input.total_rows.toLocaleString()} 行`, plural(input.total_rows, 'line', 'lines'))}
              </span>
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
                    title={blockedHint(step) ?? undefined}
                    onClick={() => onSelect(step.index)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') onSelect(step.index)
                      if (e.altKey && e.key === 'ArrowUp' && step.index > 0) onMove(step.index, step.index - 1)
                      if (e.altKey && e.key === 'ArrowDown' && step.index < last) onMove(step.index, step.index + 1)
                    }}
                    className={`flex cursor-pointer items-start gap-1.5 rounded-md px-1.5 py-1.5 text-sm hover:bg-muted ${selected ? 'bg-primary/10 ring-1 ring-primary/40' : ''} ${step.enabled ? (step.status === 'pending' ? 'opacity-60' : '') : 'opacity-50'}`}
                  >
                    <GripVertical className="mt-0.5 size-4 shrink-0 cursor-grab text-muted-foreground/50" />
                    <span className="mt-0.5 w-5 shrink-0 text-right text-xs text-muted-foreground tabular-nums">{step.index + 1}</span>
                    <span className="min-w-0 flex-1">
                      {detailed ? null : (
                        <span className="flex min-w-0 items-center gap-1">
                          <span className={`min-w-0 truncate font-medium ${step.enabled ? '' : 'line-through'}`}>{step.label}</span>
                          {step.status === 'error' ? (
                            <Badge variant="destructive" className="h-4 shrink-0 px-1 text-[10px]">
                              <CircleX /> {tr('錯誤', 'Error')}
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
                          {step.status === 'error'
                            ? (step.run_error ?? step.errors[0]?.error ?? tr('參數有誤', 'Invalid parameters'))
                            : (step.describe ?? '')}
                        </span>
                      ) : null}
                      {detailed ? (
                        <span className="mt-0.5 flex flex-wrap items-center gap-1">
                          {rows ? <span className="text-xs whitespace-nowrap text-muted-foreground tabular-nums">{rows}</span> : null}
                          {step.files !== null ? (
                            <span className="text-xs whitespace-nowrap text-muted-foreground tabular-nums">
                              {tr(`${step.files} 個來源檔`, plural(step.files, 'source file', 'source files'))}
                            </span>
                          ) : null}
                          {step.status === 'error' ? (
                            <Badge variant="destructive" className="h-4 px-1 text-[10px]">
                              <CircleX /> {tr('錯誤', 'Error')}
                            </Badge>
                          ) : null}
                          {step.status === 'pending' ? (
                            <span className="text-xs text-muted-foreground">
                              {tr(`（${blockedHint(step)}）`, `(${blockedHint(step)})`)}
                            </span>
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
                        aria-label={step.enabled ? tr('停用', 'Disable') : tr('啟用', 'Enable')}
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
                        aria-label={tr('刪除', 'Delete')}
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
                  <ArrowUp /> {tr('上移', 'Move up')}
                </ContextMenuItem>
                <ContextMenuItem disabled={step.index === last} onSelect={() => onMove(step.index, step.index + 1)}>
                  <ArrowDown /> {tr('下移', 'Move down')}
                </ContextMenuItem>
                <ContextMenuItem onSelect={() => onToggle(step.index)}>
                  {step.enabled ? <Pause /> : <Play />} {step.enabled ? tr('停用', 'Disable') : tr('啟用', 'Enable')}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem variant="destructive" onSelect={() => onDelete(step.index)}>
                  <Trash2 /> {tr('刪除', 'Delete')}
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
              <span className="block font-medium">{stage && !stage.last ? tr('階段結果', 'Stage result') : tr('輸出資料', 'Output')}</span>
              {detailed && stage && !stage.last ? (
                <span className="block text-xs text-muted-foreground">
                  {tr(`暫存，後面的階段用 @${stage.name} 讀它`, `Temporary; later stages read it as @${stage.name}`)}
                </span>
              ) : null}
            </span>
          </button>
        </li>
      </ol>
    </div>
  )
}
