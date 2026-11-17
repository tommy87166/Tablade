/**
 * 最左邊的階段面板（可以調寬度、收合）：Recipe 由好幾個依序執行的階段組成，一次編輯一個。
 * 新增階段時一定要命名（預設插在目前階段的前面）；改名、移動、刪除在每個階段旁邊的「⋯」按鈕
 * （也是右鍵選單）。點階段名稱只會切換階段，不會改名。
 * 會弄壞引用的刪除與移動由 Python 擋下來並說明（session.py）。
 */
import { ArrowDown, ArrowUp, Ellipsis, FileOutput, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState, type ComponentType, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { CollapseButton, PaneHeader, PaneTitle } from './Pane'
import type { StageRun, StageState, ViewState } from '../bridge'
import { StageRunIcon } from './RunDialog'

const MAX_NAME = 40

interface Props {
  state: ViewState
  busy: boolean
  onSelect: (index: number) => void
  /** 回傳 true 代表成功（關掉對話框） */
  onAdd: (name: string, before: boolean) => Promise<boolean>
  onRename: (index: number, name: string) => Promise<boolean>
  onMove: (index: number, to: number) => void
  onDelete: (index: number) => void
  onCollapse: () => void
  /** 處理中每個階段的狀態：名稱前面的圓點換成轉圈、打勾、紅叉 */
  runs?: StageRun[] | null
}

type Naming = { mode: 'add' } | { mode: 'rename'; stage: StageState } | null

const DOT = { ok: 'bg-green-600', waiting: 'bg-muted-foreground/40', error: 'bg-destructive' }

/** 右鍵選單與「⋯」選單共用同一組項目（兩種選單元件的介面相同）。 */
interface MenuParts {
  Item: ComponentType<{ variant?: 'default' | 'destructive'; disabled?: boolean; onSelect?: () => void; children: ReactNode }>
  Label: ComponentType<{ children: ReactNode }>
  Separator: ComponentType
}

const CONTEXT: MenuParts = { Item: ContextMenuItem, Label: ContextMenuLabel, Separator: ContextMenuSeparator }
const DROPDOWN: MenuParts = { Item: DropdownMenuItem, Label: DropdownMenuLabel, Separator: DropdownMenuSeparator }

interface MenuProps {
  stage: StageState
  total: number
  onRename: () => void
  onMove: (to: number) => void
  onDelete: () => void
}

function StageMenuItems({ parts, stage, total, onRename, onMove, onDelete }: MenuProps & { parts: MenuParts }) {
  const { Item, Label, Separator } = parts
  return (
    <>
      <Label>階段「{stage.name}」</Label>
      <Separator />
      <Item onSelect={onRename}>
        <Pencil /> 改名…
      </Item>
      <Item disabled={stage.index === 0} onSelect={() => onMove(stage.index - 1)}>
        <ArrowUp /> 往前移
      </Item>
      <Item disabled={stage.last} onSelect={() => onMove(stage.index + 1)}>
        <ArrowDown /> 往後移
      </Item>
      <Separator />
      <Item variant="destructive" disabled={total === 1} onSelect={onDelete}>
        <Trash2 /> 刪除階段
      </Item>
    </>
  )
}

export function StagesBar({ state, busy, onSelect, onAdd, onRename, onMove, onDelete, onCollapse, runs }: Props) {
  const runOf = new Map((runs ?? []).map((r) => [r.name, r]))
  const [naming, setNaming] = useState<Naming>(null)
  const [name, setName] = useState('')
  const [before, setBefore] = useState(true)
  const current = state.stages[state.current]
  const open = (next: Naming) => {
    setName(next?.mode === 'rename' ? next.stage.name : '')
    setBefore(true)
    setNaming(next)
  }
  const submit = async () => {
    if (!naming || !name.trim()) return
    const ok = naming.mode === 'add' ? await onAdd(name.trim(), before) : await onRename(naming.stage.index, name.trim())
    if (ok) setNaming(null)
  }
  return (
    <nav aria-label="階段" className="flex h-full flex-col bg-muted/30">
      <PaneHeader>
        <PaneTitle>階段</PaneTitle>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="新增階段"
            title="新增階段"
            disabled={busy}
            onClick={() => open({ mode: 'add' })}
          >
            <Plus />
          </Button>
          <CollapseButton side="left" label="階段" onClick={onCollapse} />
        </span>
      </PaneHeader>
      <ul role="tablist" aria-orientation="vertical" className="min-h-0 flex-1 overflow-auto p-1">
        {state.stages.map((stage) => {
          const selected = stage.index === state.current
          const menu: MenuProps = {
            stage,
            total: state.stages.length,
            onRename: () => open({ mode: 'rename', stage }),
            onMove: (to) => onMove(stage.index, to),
            onDelete: () => onDelete(stage.index),
          }
          return (
            <li key={stage.name} className="group">
              <ContextMenu>
                <ContextMenuTrigger asChild>
                  <div
                    className={`flex items-center rounded-md pr-0.5 ${
                      selected ? 'bg-background font-medium shadow-sm ring-1 ring-border' : 'hover:bg-muted'
                    }`}
                  >
                    <button
                      type="button"
                      role="tab"
                      aria-selected={selected}
                      title={`${stage.name}${stage.last ? '（結果寫到輸出檔）' : '（暫存，給後面的階段用）'}`}
                      className="flex min-w-0 flex-1 items-center gap-1.5 py-1.5 pl-2 text-left text-sm"
                      onClick={() => !selected && onSelect(stage.index)}
                    >
                      {runOf.has(stage.name) ? (
                        <StageRunIcon status={runOf.get(stage.name)!.status} className="size-3" />
                      ) : (
                        <span className={`size-1.5 shrink-0 rounded-full ${DOT[stage.status]}`} />
                      )}
                      <span className="min-w-0 flex-1 truncate">{stage.name}</span>
                      {stage.last ? <FileOutput className="size-3.5 shrink-0 text-muted-foreground" aria-label="輸出" /> : null}
                    </button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={`階段「${stage.name}」：改名、移動、刪除`}
                          title="改名、移動、刪除"
                          className={
                            selected
                              ? 'shrink-0'
                              : 'shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100'
                          }
                        >
                          <Ellipsis />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent side="right" align="start">
                        <StageMenuItems parts={DROPDOWN} {...menu} />
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </ContextMenuTrigger>
                <ContextMenuContent>
                  <StageMenuItems parts={CONTEXT} {...menu} />
                </ContextMenuContent>
              </ContextMenu>
            </li>
          )
        })}
      </ul>
      <p className="border-t p-2 text-[11px] leading-snug text-muted-foreground">階段依序執行，由最後一個階段輸出檔案。</p>

      <Dialog open={naming !== null} onOpenChange={(v) => !v && setNaming(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{naming?.mode === 'rename' ? '階段改名' : '新增階段'}</DialogTitle>
            <DialogDescription>
              {naming?.mode === 'rename'
                ? '引用這個階段的地方（讀取資料、結合的另一張表）會一起改。'
                : '階段名稱也是引用名稱：後面的階段用 @名稱 讀它的結果。'}
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault()
              void submit()
            }}
          >
            <Input autoFocus value={name} maxLength={MAX_NAME} placeholder="例如 門市對照" onChange={(e) => setName(e.target.value)} />
            {naming?.mode === 'add' && current ? (
              <div className="flex gap-1.5" role="radiogroup" aria-label="放在哪裡">
                {[true, false].map((b) => (
                  <Button
                    key={String(b)}
                    type="button"
                    role="radio"
                    aria-checked={before === b}
                    size="sm"
                    variant={before === b ? 'secondary' : 'ghost'}
                    className="flex-1"
                    onClick={() => setBefore(b)}
                  >
                    放在「{current.name}」{b ? '前面' : '後面'}
                  </Button>
                ))}
              </div>
            ) : null}
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setNaming(null)}>
                取消
              </Button>
              <Button type="submit" disabled={busy || !name.trim()}>
                {naming?.mode === 'rename' ? '改名' : '新增'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </nav>
  )
}
