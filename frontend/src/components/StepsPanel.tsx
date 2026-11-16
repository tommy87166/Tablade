/** 左側：讀取資料 → 步驟清單（點選、拖曳排序、停用、刪除）→ 輸出資料，以及新增步驟選單。 */
import { AlertTriangle, ArrowDown, ArrowUp, CircleX, Eye, EyeOff, FileInput, FileOutput, GripVertical, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuTrigger } from '@/components/ui/context-menu'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
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

function rowsText(rows: number | null, delta: number | null) {
  if (rows === null) return null
  const change = delta === null || delta === 0 ? '' : ` (${delta > 0 ? '+' : ''}${delta.toLocaleString()})`
  return `${rows.toLocaleString()} 列${change}`
}

export function AddStepMenu({ catalog, onAdd, disabled }: { catalog: CatalogItem[]; onAdd: (type: string) => void; disabled?: boolean }) {
  const groups = new Map<string, CatalogItem[]>()
  for (const item of catalog) groups.set(item.category, [...(groups.get(item.category) ?? []), item])
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="xs" disabled={disabled}>
          <Plus /> 新增步驟
        </Button>
      </DropdownMenuTrigger>
      {/* 每個分類一欄：列、欄並排 */}
      <DropdownMenuContent className="grid w-[36rem] max-w-[calc(100vw-2rem)] grid-cols-2 gap-x-3" align="start">
        {[...groups.entries()].map(([category, items]) => (
          <DropdownMenuGroup key={category}>
            <DropdownMenuLabel>{category}</DropdownMenuLabel>
            {items.map((item) => (
              <DropdownMenuItem
                key={item.type}
                onSelect={() => onAdd(item.type)}
                className="my-1 rounded-l-none border-l-2 border-muted-foreground/50 pl-2.5 focus:border-primary"
              >
                <div>
                  <div>{item.label}</div>
                  <StepScope scope={item.scope} />
                  <div className="text-xs text-muted-foreground">{item.description}</div>
                </div>
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function StepsPanel({ state, catalog, busy, onSelect, onAdd, onMove, onToggle, onDelete, onCollapse }: Props) {
  const [dragging, setDragging] = useState<number | null>(null)
  const [over, setOver] = useState<number | null>(null)
  const input = state.input
  const last = state.steps.length - 1

  return (
    <div className="flex h-full flex-col">
      <PaneHeader>
        <PaneTitle>步驟</PaneTitle>
        {state.input.sampled ? <span className="truncate text-[11px] text-amber-700 dark:text-amber-400">列數為樣本</span> : null}
        <span className="ml-auto flex shrink-0 items-center gap-1">
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
              <span className="block truncate text-xs text-muted-foreground">
                {input.error ?? `${input.pattern}（${input.files.length} 個檔案）`}
              </span>
            </span>
            {input.total_rows !== null ? (
              <span className="text-xs text-muted-foreground tabular-nums">{input.total_rows.toLocaleString()} 行</span>
            ) : null}
          </button>
        </li>
        {state.steps.map((step) => {
          const selected = state.selected === step.index
          const rows = rowsText(step.rows, step.delta)
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
                      <span className={`block font-medium ${step.enabled ? '' : 'line-through'}`}>{step.label}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {step.status === 'error' ? (step.run_error ?? step.errors[0]?.error ?? '參數有誤') : (step.describe ?? '')}
                      </span>
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
                          <Badge variant="outline" className="h-4 border-amber-500/50 px-1 text-[10px] text-amber-700 dark:text-amber-400">
                            <AlertTriangle /> {step.warning_count.toLocaleString()}
                          </Badge>
                        ) : null}
                      </span>
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
                        {step.enabled ? <Eye /> : <EyeOff />}
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
                  {step.enabled ? <EyeOff /> : <Eye />} {step.enabled ? '停用' : '啟用'}
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
              <span className="block font-medium">輸出資料</span>
              <span className="block truncate text-xs text-muted-foreground">Parquet・執行時選擇位置</span>
            </span>
          </button>
        </li>
      </ol>
    </div>
  )
}
