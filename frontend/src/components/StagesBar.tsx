/**
 * 最左邊的階段分頁：Recipe 由好幾個依序執行的階段組成，一次編輯一個。
 * 新增階段時一定要命名（預設插在目前階段的前面）；改名、移動、刪除在右鍵選單。
 * 會弄壞引用的刪除與移動由 Python 擋下來並說明（session.py）。
 */
import { ArrowDown, ArrowUp, FileOutput, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

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
import { Input } from '@/components/ui/input'
import type { StageState, ViewState } from '../bridge'

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
}

type Naming = { mode: 'add' } | { mode: 'rename'; stage: StageState } | null

const DOT = { ok: 'bg-green-600', waiting: 'bg-muted-foreground/40', error: 'bg-destructive' }

export function StagesBar({ state, busy, onSelect, onAdd, onRename, onMove, onDelete }: Props) {
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
    <nav aria-label="階段" className="flex w-32 shrink-0 flex-col border-r bg-muted/30">
      <div className="flex h-9 items-center justify-between border-b px-2">
        <span className="text-xs font-medium text-muted-foreground">階段</span>
        <Button variant="ghost" size="icon-xs" aria-label="新增階段" title="新增階段" disabled={busy} onClick={() => open({ mode: 'add' })}>
          <Plus />
        </Button>
      </div>
      <ul role="tablist" aria-orientation="vertical" className="min-h-0 flex-1 overflow-auto p-1">
        {state.stages.map((stage) => (
          <li key={stage.name}>
            <ContextMenu>
              <ContextMenuTrigger asChild>
                <button
                  type="button"
                  role="tab"
                  aria-selected={stage.index === state.current}
                  title={`${stage.name}${stage.last ? '（結果寫到輸出檔）' : '（暫存，給後面的階段用）'}`}
                  className={`flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-sm ${
                    stage.index === state.current ? 'bg-background font-medium shadow-sm ring-1 ring-border' : 'hover:bg-muted'
                  }`}
                  onClick={() => stage.index !== state.current && onSelect(stage.index)}
                >
                  <span className={`size-1.5 shrink-0 rounded-full ${DOT[stage.status]}`} />
                  <span className="min-w-0 flex-1 truncate">{stage.name}</span>
                  {stage.last ? <FileOutput className="size-3.5 shrink-0 text-muted-foreground" aria-label="輸出" /> : null}
                </button>
              </ContextMenuTrigger>
              <ContextMenuContent>
                <ContextMenuLabel>階段「{stage.name}」</ContextMenuLabel>
                <ContextMenuSeparator />
                <ContextMenuItem onSelect={() => open({ mode: 'rename', stage })}>
                  <Pencil /> 改名…
                </ContextMenuItem>
                <ContextMenuItem disabled={stage.index === 0} onSelect={() => onMove(stage.index, stage.index - 1)}>
                  <ArrowUp /> 往前移
                </ContextMenuItem>
                <ContextMenuItem disabled={stage.last} onSelect={() => onMove(stage.index, stage.index + 1)}>
                  <ArrowDown /> 往後移
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem variant="destructive" disabled={state.stages.length === 1} onSelect={() => onDelete(stage.index)}>
                  <Trash2 /> 刪除階段
                </ContextMenuItem>
              </ContextMenuContent>
            </ContextMenu>
          </li>
        ))}
      </ul>
      <p className="border-t p-2 text-[11px] leading-snug text-muted-foreground">依序執行；最後一個階段的結果寫到輸出檔。</p>

      <Dialog open={naming !== null} onOpenChange={(v) => !v && setNaming(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{naming?.mode === 'rename' ? '階段改名' : '新增階段'}</DialogTitle>
            <DialogDescription>
              {naming?.mode === 'rename'
                ? '引用這個階段的地方（讀取資料、合併查詢的右表）會一起改。'
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
