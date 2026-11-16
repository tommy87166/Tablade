/** 右側（選取「原始資料」時）：recipe 名稱、input 設定、來源檔清單、輸出設定。 */
import { FolderOpen } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import type { ViewState } from '../bridge'

const READERS = { text_lines: '逐行讀取（爛格式檔案）', csv: '乾淨的 CSV' }
const ENCODINGS = { auto: '自動偵測（UTF-8 → cp950）', 'utf-8': 'UTF-8', 'utf-8-sig': 'UTF-8（BOM）', cp950: 'cp950（Big5）', big5hkscs: 'Big5-HKSCS' }

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/**
 * 輸入框：停止輸入後才送出，避免每個字都觸發重算。
 * Python 回傳的值如果就是自己剛送出的，不覆蓋草稿（使用者可能已經繼續打字）；
 * 其他來源的變更（undo、開啟 recipe）才以 Python 的值為準。
 */
function LazyInput({ value, onCommit, placeholder }: { value: string; onCommit: (v: string) => void; placeholder?: string }) {
  const [draft, setDraft] = useState(value)
  const [seen, setSeen] = useState(value)
  const [committed, setCommitted] = useState(value)
  const timer = useRef<number | undefined>(undefined)
  if (value !== seen) {
    setSeen(value)
    if (value !== committed) setDraft(value)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const commit = (next: string) => {
    setCommitted(next)
    onCommit(next)
  }
  return (
    <Input
      value={draft}
      placeholder={placeholder}
      onChange={(e) => {
        const next = e.target.value
        setDraft(next)
        window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => commit(next), 500)
      }}
      onBlur={() => {
        window.clearTimeout(timer.current)
        if (draft !== committed) commit(draft)
      }}
    />
  )
}

interface Props {
  state: ViewState
  onOpenData: () => void
  onInput: (values: Partial<ViewState['input']>) => void
  onOutput: (values: Partial<ViewState['output']>) => void
  onMeta: (values: { name?: string }) => void
}

export function SourcePanel({ state, onOpenData, onInput, onOutput, onMeta }: Props) {
  const { input, output, recipe } = state
  return (
    <div className="space-y-5">
      <section className="space-y-1.5">
        <div className="text-sm font-medium">recipe 名稱</div>
        <LazyInput value={recipe.name} onCommit={(name) => onMeta({ name })} placeholder="例如：門市銷售明細整理" />
        <p className="text-xs text-muted-foreground">{recipe.path ?? '尚未存檔'}</p>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium">原始資料</div>
          <Button size="sm" variant="outline" onClick={onOpenData}>
            <FolderOpen /> 選擇檔案…
          </Button>
        </div>
        <div className="space-y-1.5">
          <div className="text-xs text-muted-foreground">檔案（相對於 recipe 所在資料夾，可用 * 一次讀多個檔）</div>
          <LazyInput value={input.pattern} onCommit={(pattern) => onInput({ pattern })} placeholder="例如 data/明細_*.csv" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1.5">
            <div className="text-xs text-muted-foreground">讀取方式</div>
            <Select value={input.reader} onValueChange={(reader) => onInput({ reader: reader as ViewState['input']['reader'] })}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(READERS).map(([k, v]) => (
                  <SelectItem key={k} value={k}>
                    {v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <div className="text-xs text-muted-foreground">編碼</div>
            <Select value={input.encoding} onValueChange={(encoding) => onInput({ encoding })}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(ENCODINGS).map(([k, v]) => (
                  <SelectItem key={k} value={k}>
                    {v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        {input.error ? <p className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{input.error}</p> : null}
        {input.files.length ? (
          <ul className="divide-y rounded-md border text-xs">
            {input.files.map((f) => (
              <li key={f.path} className="flex items-center justify-between gap-2 px-2 py-1.5">
                <span className="min-w-0 truncate">{f.path}</span>
                <span className="shrink-0 text-muted-foreground tabular-nums">
                  {f.encoding}・{f.rows.toLocaleString()} 列・{formatBytes(f.bytes)}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="space-y-3">
        <div className="text-sm font-medium">輸出</div>
        <div className="space-y-1.5">
          <div className="text-xs text-muted-foreground">Parquet 檔（相對於 recipe 所在資料夾）</div>
          <LazyInput value={output.path} onCommit={(path) => onOutput({ path })} />
        </div>
        <label className="flex items-center justify-between gap-3 text-sm">
          保留系統欄位（__source_file、__line_no）
          <Switch checked={output.keep_system_columns} onCheckedChange={(keep_system_columns) => onOutput({ keep_system_columns })} />
        </label>
      </section>
    </div>
  )
}
