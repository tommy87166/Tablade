/**
 * 語法選擇器（「插入語法…」的對話框；docs/custom-expression-plan.md）。
 *
 * 左邊是分類（照語法表的順序），右邊是那一類的每一項：寫法（有高亮）、說明、例子；上面的搜尋框跨分類找
 * （比對寫法、說明、例子，不分大小寫）。點一項或用方向鍵選到後按 Enter 就插入；Esc 或點外面關掉、不插入。
 * 項目全部來自 schema 的 ui_syntax（Python 的語法表，已經換成畫面的語言），這裡不寫死任何一項。
 */
import { Search } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { tr } from '@/i18n'
import { tokenize, type Vocabulary } from '@/lib/expression'
import type { SyntaxEntry } from '../../bridge'
import { Highlighted } from './Highlighted'

export function SyntaxPicker({
  open,
  onOpenChange,
  syntax,
  vocab,
  onPick,
  onClosed,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  syntax: SyntaxEntry[]
  vocab: Vocabulary
  onPick: (item: SyntaxEntry) => void
  /** 對話框關好了（焦點交還給編輯框） */
  onClosed?: () => void
}) {
  const categories = useMemo(() => [...new Set(syntax.map((s) => s.category))], [syntax])
  const [category, setCategory] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const list = useRef<HTMLDivElement>(null)
  const current = category ?? categories[0] ?? ''
  const needle = query.trim().toLowerCase()
  const items = needle
    ? syntax.filter((s) => [s.insert, s.doc, s.example].some((t) => t.toLowerCase().includes(needle)))
    : syntax.filter((s) => s.category === current)
  const index = Math.min(active, Math.max(0, items.length - 1))

  const move = (to: number) => {
    const next = Math.max(0, Math.min(items.length - 1, to))
    setActive(next)
    list.current?.querySelector<HTMLElement>(`[data-index="${next}"]`)?.scrollIntoView({ block: 'nearest' })
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setQuery('')
          setActive(0)
        }
        onOpenChange(next)
      }}
    >
      <DialogContent className="flex h-[min(36rem,85vh)] flex-col gap-3 sm:max-w-3xl" onCloseAutoFocus={(e) => {
          e.preventDefault()
          onClosed?.()
        }}
      >
        <DialogHeader>
          <DialogTitle>{tr('插入語法', 'Insert syntax')}</DialogTitle>
          <DialogDescription>
            {tr('「自訂」只能用這裡列出的寫法。點一項插到運算式裡游標的位置。', 'Custom steps can only use the syntax listed here. Click an item to insert it at the cursor.')}
          </DialogDescription>
        </DialogHeader>
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            autoFocus
            value={query}
            className="pl-8"
            placeholder={tr('搜尋寫法、說明或例子', 'Search syntax, descriptions or examples')}
            aria-label={tr('搜尋語法', 'Search syntax')}
            onChange={(e) => {
              setQuery(e.target.value)
              setActive(0)
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                move(index + 1)
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                move(index - 1)
              } else if (e.key === 'Enter' && items[index]) {
                e.preventDefault()
                onPick(items[index])
              }
            }}
          />
        </div>
        <div className="flex min-h-0 flex-1 gap-3">
          <div role="tablist" aria-orientation="vertical" className="flex w-40 shrink-0 flex-col gap-0.5 overflow-y-auto">
            {categories.map((name) => {
              const selected = !needle && name === current
              return (
                <button
                  key={name}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  className={`rounded-md px-2 py-1 text-left text-sm ${
                    selected ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60'
                  }`}
                  onClick={() => {
                    setCategory(name)
                    setQuery('')
                    setActive(0)
                  }}
                >
                  {name}
                  <span className="ml-1 text-xs text-muted-foreground tabular-nums">{syntax.filter((s) => s.category === name).length}</span>
                </button>
              )
            })}
          </div>
          <div ref={list} role="listbox" aria-label={tr('寫法', 'Syntax')} className="min-w-0 flex-1 space-y-1 overflow-y-auto pr-1">
            {items.length === 0 ? <p className="p-2 text-sm text-muted-foreground">{tr('沒有符合的寫法', 'No matching syntax')}</p> : null}
            {items.map((item, i) => (
              <button
                key={item.id}
                type="button"
                role="option"
                aria-selected={i === index}
                data-index={i}
                className={`block w-full rounded-md border px-3 py-2 text-left ${
                  i === index ? 'border-ring bg-muted/60' : 'border-transparent hover:bg-muted/40'
                }`}
                onMouseEnter={() => setActive(i)}
                onFocus={() => setActive(i)}
                onClick={() => onPick(item)}
              >
                <div className="font-mono text-sm whitespace-pre-wrap">
                  <Highlighted tokens={tokenize(item.insert.trim() || item.insert, vocab, null)} />
                </div>
                <div className="mt-0.5 text-xs">{item.doc}</div>
                <div className="mt-0.5 font-mono text-xs break-all text-muted-foreground">
                  {tr('例如：', 'e.g. ')}
                  {item.example}
                </div>
              </button>
            ))}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
