/**
 * 「自訂」的運算式欄位（ui: "expression"；docs/custom-expression-plan.md）。
 *
 * - 編輯框是原生的 textarea（中文輸入法、複製貼上、復原照舊），疊在畫好顏色的 <pre> 上：textarea 的字透明、
 *   游標看得見；兩層的字型、字級、行高、內距、邊框、換行方式完全一樣，捲動時 <pre> 跟著捲。
 * - 「插入欄位」插 pl.col("欄名")、「插入語法…」開語法選擇器；插在游標的位置（有選取時取代），沒點過時接在最後面。
 * - 函式的名稱、分類、說明都從 schema 的 ui_syntax 來（Python 的語法表），這裡不寫死。
 * - 高亮只是幫忙看，對錯以 Python 的驗證為準（草稿停止輸入後送出，照表單原本的流程）。
 */
import { Braces, Columns3 } from 'lucide-react'
import { useLayoutEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { tr } from '@/i18n'
import { insertAt, quote, tokenize, vocabulary } from '@/lib/expression'
import type { SyntaxEntry } from '../../bridge'
import { Highlighted } from './Highlighted'
import { SyntaxPicker } from './SyntaxPicker'

/** 兩層共用的樣式：一個字都不能差，不然游標和字會對不上 */
const LAYER =
  'm-0 box-border w-full rounded-md border px-3 py-2 font-mono text-sm leading-5 whitespace-pre-wrap [overflow-wrap:break-word] [scrollbar-gutter:stable] [tab-size:4]'
const MIN_ROWS = 3
const MAX_ROWS = 12
/** 一行的高度（leading-5）與上下內距＋邊框（py-2、border） */
const LINE_PX = 20
const EXTRA_PX = 16 + 2

export function ExpressionField({
  value,
  onChange,
  columns,
  systemColumns,
  syntax,
}: {
  value: unknown
  onChange: (v: unknown) => void
  columns: string[] | null
  systemColumns: string[] | null
  syntax: SyntaxEntry[]
}) {
  const text = typeof value === 'string' ? value : ''
  const area = useRef<HTMLTextAreaElement>(null)
  const layer = useRef<HTMLPreElement>(null)
  /** 編輯框最後的游標與選取範圍（打開選單、對話框之前記下來；沒點過是 null：接在最後面） */
  const selection = useRef<{ start: number; end: number } | null>(null)
  const [picking, setPicking] = useState(false)
  const vocab = useMemo(() => vocabulary(syntax), [syntax])
  const known = useMemo(() => (columns ? [...columns, ...(systemColumns ?? [])] : null), [columns, systemColumns])
  const tokens = useMemo(() => tokenize(text, vocab, known), [text, vocab, known])

  // 高度跟著內容長高（最少 3 行、最多 12 行，超過時捲動）
  useLayoutEffect(() => {
    const el = area.current
    if (!el) return
    el.style.height = 'auto'
    const height = Math.min(Math.max(el.scrollHeight, MIN_ROWS * LINE_PX + EXTRA_PX), MAX_ROWS * LINE_PX + EXTRA_PX)
    el.style.height = `${height}px`
    if (layer.current) layer.current.scrollTop = el.scrollTop
  }, [text])

  const remember = () => {
    const el = area.current
    if (el) selection.current = { start: el.selectionStart, end: el.selectionEnd }
  }

  /** 插入之後要把焦點與游標放回編輯框（選單、對話框關好之後；它們關的時候會搶焦點） */
  const restore = useRef<number | null>(null)
  const refocus = () => {
    const el = area.current
    const cursor = restore.current
    if (!el || cursor === null) return
    restore.current = null
    el.focus()
    el.setSelectionRange(cursor, cursor)
  }

  const insert = (snippet: string, caret: number = snippet.length) => {
    const at = selection.current ?? { start: text.length, end: text.length }
    const next = insertAt(text, at.start, at.end, snippet, caret)
    onChange(next.text)
    selection.current = { start: next.cursor, end: next.cursor }
    restore.current = next.cursor
  }

  const columnItem = (name: string) => (
    <DropdownMenuItem key={name} className="font-mono text-xs" onSelect={() => insert(`pl.col(${quote(name)})`)}>
      {name}
    </DropdownMenuItem>
  )

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1.5">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="xs" onMouseDown={remember}>
              <Columns3 /> {tr('插入欄位', 'Insert column')}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            className="max-h-72 overflow-y-auto"
            onCloseAutoFocus={(e) => {
              e.preventDefault()
              refocus()
            }}
          >
            {columns === null ? (
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                {tr('還不知道這一步有哪些欄（先載入資料）', "This step's columns aren't known yet (load data first)")}
              </DropdownMenuLabel>
            ) : (
              <>
                <DropdownMenuGroup>
                  <DropdownMenuLabel className="text-xs text-muted-foreground">{tr('資料欄', 'Data columns')}</DropdownMenuLabel>
                  {columns.map(columnItem)}
                </DropdownMenuGroup>
                {systemColumns?.length ? (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuGroup>
                      <DropdownMenuLabel className="text-xs text-muted-foreground">{tr('系統欄位', 'System columns')}</DropdownMenuLabel>
                      {systemColumns.map(columnItem)}
                    </DropdownMenuGroup>
                  </>
                ) : null}
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        <Button
          variant="outline"
          size="xs"
          onMouseDown={remember}
          onClick={() => {
            remember()
            setPicking(true)
          }}
        >
          <Braces /> {tr('插入語法…', 'Insert syntax…')}
        </Button>
      </div>
      <div className="relative rounded-md bg-background">
        <pre ref={layer} aria-hidden className={`${LAYER} pointer-events-none absolute inset-0 overflow-hidden border-transparent`}>
          <Highlighted tokens={tokens} />
          {/* 最後是換行時 <pre> 要多一個字才會一樣高 */}
          {text.endsWith('\n') || text === '' ? ' ' : null}
        </pre>
        <textarea
          ref={area}
          value={text}
          rows={MIN_ROWS}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          autoComplete="off"
          aria-label={tr('運算式', 'Expression')}
          className={`${LAYER} relative block resize-none overflow-y-auto border-input bg-transparent text-transparent caret-foreground outline-none selection:bg-primary/25 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50`}
          onChange={(e) => {
            onChange(e.target.value)
            selection.current = { start: e.target.selectionStart, end: e.target.selectionEnd }
          }}
          onSelect={remember}
          onBlur={remember}
          onScroll={(e) => {
            if (layer.current) layer.current.scrollTop = e.currentTarget.scrollTop
          }}
        />
      </div>
      <SyntaxPicker
        open={picking}
        onOpenChange={setPicking}
        syntax={syntax}
        vocab={vocab}
        onClosed={refocus}
        onPick={(item) => {
          setPicking(false)
          insert(item.insert, item.caret)
        }}
      />
    </div>
  )
}
