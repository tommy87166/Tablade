/** 右側設定：選取「讀取資料」時是檔案清單與讀法，選取「輸出資料」時是輸出設定。 */
import { CircleCheck, CircleX, FileInput, FolderOpen, Plus, X } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import type { StageState, ViewState } from '../bridge'

const READERS = {
  text_lines: '逐行讀取（爛格式檔案）',
  csv: '乾淨的 CSV',
  excel: 'Excel 工作表',
  parquet: 'Parquet',
  stages: '前面階段的輸出',
}
/** Radix 的選項不能是空字串：「第一個工作表」用這個值代表 sheet = '' */
const FIRST_SHEET = '__first__'
/** 全部工作表（Excel 的工作表名稱不能有 *，不會撞名） */
const ALL_SHEETS = '*'
const ENCODINGS = {
  auto: '自動偵測（UTF-8 → cp950）',
  'utf-8': 'UTF-8',
  'utf-8-sig': 'UTF-8（BOM）',
  cp950: 'cp950（Big5）',
  big5hkscs: 'Big5-HKSCS',
}

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

interface InputProps {
  state: ViewState
  busy: boolean
  onLoad: () => void
  onAddFiles: () => void
  onAddEntry: (text: string) => void
  onRemove: (index: number) => void
  onInput: (values: Partial<ViewState['input']>) => void
}

const isGlob = (path: string) => /[*?[]/.test(path)

/** 讀前面階段的輸出：勾選要讀的階段（依階段的順序讀）。 */
function StagesInput({ state, busy, onLoad, onInput }: Pick<InputProps, 'state' | 'busy' | 'onLoad' | 'onInput'>) {
  const { input } = state
  const picked = new Set(input.stages)
  const toggle = (name: string, on: boolean) => onInput({ stages: input.earlier.filter((n) => (n === name ? on : picked.has(n))) })
  const missing = input.stages.filter((n) => !input.earlier.includes(n))
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <div className="text-xs text-muted-foreground">要讀的階段（依階段的順序接在一起）</div>
        <ul className="divide-y rounded-md border text-sm">
          {input.earlier.map((name) => (
            <li key={name}>
              <label className="flex items-center gap-2 px-2 py-1.5">
                <Checkbox checked={picked.has(name)} onCheckedChange={(v) => toggle(name, v === true)} />
                <span className="min-w-0 flex-1 truncate">@{name}</span>
              </label>
            </li>
          ))}
        </ul>
        {missing.length ? <p className="text-xs text-destructive">不是前面的階段：{missing.join('、')}</p> : null}
        <p className="text-xs text-muted-foreground">
          每個來源檔各自是一個來源（名稱是「@階段／檔名」）。要接成一份（附加）就加「合併來源檔」；同名欄的型別要一樣，缺的欄補空值。
        </p>
      </div>
      {input.error ? <p className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{input.error}</p> : null}
      {!input.loaded && !input.error ? (
        <div className="space-y-1.5">
          <Button className="w-full" onClick={onLoad} disabled={busy || !input.can_load || !input.stages.length}>
            <FileInput /> 載入資料
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            {input.stages.length ? '前面的階段還沒有結果：載入資料，並確認前面的階段沒有錯誤' : '先勾選要讀的階段'}
          </p>
        </div>
      ) : input.loaded ? (
        <p className="text-xs text-muted-foreground">
          {input.rows?.toLocaleString()} 列 × {input.columns?.length} 欄
        </p>
      ) : null}
    </div>
  )
}

export function InputPanel({ state, busy, onLoad, onAddFiles, onAddEntry, onRemove, onInput }: InputProps) {
  const { input } = state
  const [entry, setEntry] = useState('')
  const readerSelect = (
    <div className="space-y-1.5">
      <div className="text-xs text-muted-foreground">讀取方式</div>
      <Select value={input.reader} onValueChange={(reader) => onInput({ reader: reader as ViewState['input']['reader'] })}>
        <SelectTrigger className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.entries(READERS).map(([k, v]) => (
            <SelectItem key={k} value={k} disabled={k === 'stages' && state.current === 0}>
              {v}
              {k === 'stages' && state.current === 0 ? '（第一個階段不能選）' : ''}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
  if (input.reader === 'stages') {
    return (
      <section className="space-y-3">
        <div className="text-sm font-medium">讀取資料</div>
        {readerSelect}
        <StagesInput state={state} busy={busy} onLoad={onLoad} onInput={onInput} />
      </section>
    )
  }
  const loadHint = !input.files.length
    ? '先加入檔案'
    : !input.can_load
      ? '檔案清單有找不到的檔，請先移除或修正'
      : input.stale
        ? '設定已變更，請重新載入'
        : ''
  const hintTone =
    !input.can_load && input.files.length
      ? 'text-destructive'
      : input.stale
        ? 'text-amber-700 dark:text-amber-400'
        : 'text-muted-foreground'
  const submit = () => {
    if (!entry.trim()) return
    onAddEntry(entry)
    setEntry('')
  }
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium">讀取資料</div>
        <Button size="sm" variant="outline" onClick={onAddFiles}>
          <FolderOpen /> 瀏覽檔案…
        </Button>
      </div>
      <div className="space-y-1.5">
        <div className="text-xs text-muted-foreground">檔案清單</div>
        {input.entries.length ? (
          <ul className="divide-y rounded-md border text-sm">
            {input.entries.map((e, i) => (
              <li key={e.path} className="flex items-center gap-2 py-1 pr-1 pl-2">
                {e.found ? (
                  <CircleCheck className="size-4 shrink-0 text-green-600" />
                ) : (
                  <CircleX className="size-4 shrink-0 text-destructive" />
                )}
                <span className="min-w-0 flex-1 truncate font-mono text-xs" title={e.path}>
                  {e.path}
                </span>
                {!e.found ? (
                  <span className="shrink-0 text-xs text-destructive">找不到</span>
                ) : isGlob(e.path) ? (
                  <span className="shrink-0 text-xs text-muted-foreground">符合 {e.found} 個檔</span>
                ) : null}
                <Button variant="ghost" size="icon-xs" aria-label={`從清單移除 ${e.path}`} title="從清單移除" onClick={() => onRemove(i)}>
                  <X />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="rounded-md border border-dashed p-3 text-center text-xs text-muted-foreground">
            還沒有檔案。按「瀏覽檔案…」選檔，或在下面輸入路徑。
          </p>
        )}
        <form
          className="flex gap-1.5"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <Input value={entry} onChange={(e) => setEntry(e.target.value)} placeholder="手動輸入路徑，例如 data/明細_*.csv" />
          <Button type="submit" size="sm" variant="outline" disabled={!entry.trim()}>
            <Plus /> 加入
          </Button>
        </form>
      </div>
      <div className="space-y-3">
        {readerSelect}
        {input.reader === 'parquet' ? (
          <p className="text-xs text-muted-foreground">
            欄位與型別照檔案；檔案自己帶的 __ 開頭欄位改名成「原始…」保留（例如 __line_no → 原始行號）。
          </p>
        ) : input.reader === 'excel' ? (
          <div className="space-y-1.5">
            <div className="text-xs text-muted-foreground">工作表</div>
            <Select value={input.sheet || FIRST_SHEET} onValueChange={(sheet) => onInput({ sheet: sheet === FIRST_SHEET ? '' : sheet })}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={FIRST_SHEET}>第一個工作表</SelectItem>
                <SelectItem value={ALL_SHEETS}>全部工作表（每個檔的每個工作表）</SelectItem>
                {[...new Set([...input.sheets, ...(input.sheet && input.sheet !== ALL_SHEETS ? [input.sheet] : [])])].map((name) => (
                  <SelectItem key={name} value={name}>
                    {input.sheets.includes(name) ? name : `${name}（找不到）`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              欄名是 A、B、C…，行號是 Excel 的列號；每一格都讀成文字。
              {input.sheet === ALL_SHEETS ? '每個工作表各自處理，系統欄位 __sheet 記工作表名稱。' : ''}
            </p>
          </div>
        ) : (
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
        )}
      </div>
      {input.error ? <p className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{input.error}</p> : null}
      {/* 載入後隱藏；檔案清單、讀取方式、編碼變了才再出現 */}
      {!input.loaded ? (
        <div className="space-y-1.5">
          <Button className="w-full" onClick={onLoad} disabled={busy || !input.can_load}>
            <FileInput /> 載入資料
          </Button>
          {loadHint ? <p className={`text-center text-xs ${hintTone}`}>{loadHint}</p> : null}
        </div>
      ) : (
        <div className="space-y-1.5">
          <div className="text-xs text-muted-foreground">已載入的檔案</div>
          <ul className="divide-y rounded-md border text-xs">
            {input.sources.map((f) => (
              <li key={`${f.path}／${f.sheet ?? ''}`} className="flex items-center justify-between gap-2 px-2 py-1.5">
                <span className="min-w-0 truncate" title={f.sheet ? `${f.path}［${f.sheet}］` : f.path}>
                  {f.path}
                  {f.sheet ? <span className="text-muted-foreground">［{f.sheet}］</span> : null}
                </span>
                <span className="shrink-0 text-muted-foreground tabular-nums">
                  {f.encoding}・{f.rows.toLocaleString()} 列・{formatBytes(f.bytes)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

interface OutputProps {
  state: ViewState
  onOutput: (values: Partial<ViewState['output']>) => void
  onStageOutput: (index: number, file: string) => void
}

const stemOf = (file: string) => file.replace(/\.parquet$/i, '')

/** 輸出檔名：只填檔名，後面固定是 .parquet；離開欄位或按 Enter 才送出（檢查在 Python 做）。 */
function OutputFileField({ stage, onChange }: { stage: StageState; onChange: (file: string) => void }) {
  const saved = stemOf(stage.output_file)
  const [draft, setDraft] = useState(saved)
  const planned = stage.output
  const commit = () => {
    if (draft.trim() !== saved) onChange(draft.trim())
  }
  return (
    <div className="space-y-1.5">
      <label className="text-sm" htmlFor={`output-file-${stage.index}`}>
        輸出檔名{stage.last ? '' : '（選填）'}
      </label>
      <div className="flex items-center gap-1.5">
        <Input
          id={`output-file-${stage.index}`}
          className="h-8"
          value={draft}
          placeholder={stage.last && planned ? stemOf(planned.file) : '不寫檔'}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.currentTarget.blur()
          }}
        />
        <span className="shrink-0 text-sm text-muted-foreground">.parquet</span>
      </div>
      {planned?.error ? (
        <p className="text-xs text-destructive">{planned.error}</p>
      ) : planned ? (
        <p className="text-xs break-all text-muted-foreground">
          寫到 {planned.path}
          {planned.default ? '（沒填時用 Recipe 的名稱）' : ''}
          {planned.exists ? <span className="text-amber-700 dark:text-amber-400">；已經有這個檔，匯出時會覆蓋</span> : null}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">填了才另外存成檔案（存到 Recipe 所在資料夾底下的 output 資料夾）。</p>
      )}
    </div>
  )
}

export function OutputPanel({ state, onOutput, onStageOutput }: OutputProps) {
  const { output } = state
  const stage = state.stages[state.current]
  // 換階段或 Python 改了檔名（例如補上 .parquet）時重新開始編輯
  const field = stage ? (
    <OutputFileField key={`${stage.index}:${stage.output_file}`} stage={stage} onChange={(file) => onStageOutput(stage.index, file)} />
  ) : null
  if (stage && !stage.last) {
    const users = state.stages.filter((s) => s.uses.includes(stage.name)).map((s) => `「${s.name}」`)
    return (
      <section className="space-y-3">
        <div className="text-sm font-medium">階段結果</div>
        <p className="text-sm text-muted-foreground">
          後面的階段用 @{stage.name} 讀這個階段的結果
          {users.length ? `（目前${users.join('、')}用到它）` : '（目前還沒有階段用到它）'}；填了輸出檔名也會另外存成檔案。
        </p>
        {field}
      </section>
    )
  }
  return (
    <section className="space-y-3">
      <div className="text-sm font-medium">輸出資料</div>
      <p className="text-xs text-muted-foreground">
        按工具列的「處理並匯出資料」處理全部資料，寫到 Recipe 所在資料夾底下的 output 資料夾。
      </p>
      {field}
      <label className="flex items-center justify-between gap-3 text-sm">
        <span>
          保留系統欄位（__source_file、__line_no 等）
          <span className="block text-xs text-muted-foreground">套用到每個輸出檔</span>
        </span>
        <Switch checked={output.keep_system_columns} onCheckedChange={(keep_system_columns) => onOutput({ keep_system_columns })} />
      </label>
      <label className="flex items-start justify-between gap-3 text-sm">
        <span>
          同時寫出處理紀錄（manifest）
          <span className="block text-xs text-muted-foreground">
            每個輸出檔在 log 資料夾各寫一份 .manifest.json：用了哪份 Recipe、輸入與輸出檔的 SHA-256、每一步的列數與警告
          </span>
        </span>
        <Switch checked={output.manifest} onCheckedChange={(manifest) => onOutput({ manifest })} />
      </label>
    </section>
  )
}
