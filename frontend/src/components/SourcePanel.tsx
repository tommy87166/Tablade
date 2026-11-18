/** 右側設定：選取「讀取資料」時是檔案清單與讀法，選取「輸出資料」時是輸出設定。 */
import { CircleAlert, CircleCheck, CircleX, FolderOpen, Plus, X } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { plural, tr } from '@/i18n'
import type { OutputFormat, StageState, ViewState, ZipScan } from '../bridge'

const readers = () => ({
  text_lines: tr('逐行讀取（爛格式檔案）', 'Line by line (messy files)'),
  zip_lines: tr('zip 裡的文字檔（逐行讀取）', 'Text files in zip (line by line)'),
  csv: tr('乾淨的 CSV', 'Clean CSV'),
  excel: tr('Excel 工作表', 'Excel sheet'),
  parquet: 'Parquet',
  stages: tr('前面階段的輸出', 'Output of earlier stages'),
})
/** Radix 的選項不能是空字串：「第一個工作表」用這個值代表 sheet = '' */
const FIRST_SHEET = '__first__'
/** 全部工作表（Excel 的工作表名稱不能有 *，不會撞名） */
const ALL_SHEETS = '*'
const encodings = () => ({
  auto: tr('自動偵測（UTF-8 → cp950）', 'Auto-detect (UTF-8 → cp950)'),
  'utf-8': 'UTF-8',
  'utf-8-sig': tr('UTF-8（BOM）', 'UTF-8 (BOM)'),
  cp950: tr('cp950（Big5）', 'cp950 (Big5)'),
  big5hkscs: 'Big5-HKSCS',
})

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

interface InputProps {
  state: ViewState
  busy: boolean
  onAddFiles: () => void
  onAddEntry: (text: string) => void
  onRemove: (index: number) => void
  onInput: (values: Partial<ViewState['input']>) => void
}

const isGlob = (path: string) => /[*?[]/.test(path)

/** zip 裡要讀的檔（members）：離開欄位或按 Enter 才送出（Python 馬上重新掃描 zip 的目錄）。 */
function MembersField({ value, onChange }: { value: string; onChange: (members: string) => void }) {
  const [draft, setDraft] = useState(value)
  const [saved, setSaved] = useState(value)
  if (saved !== value) {
    // Python 那邊的值變了（復原、換階段）：輸入框跟著換
    setSaved(value)
    setDraft(value)
  }
  const commit = () => {
    if (draft.trim() !== value) onChange(draft.trim())
  }
  return (
    <div className="space-y-1.5">
      <label className="text-xs text-muted-foreground" htmlFor="zip-members">
        {tr('zip 裡的檔', 'Files in the zip')}
      </label>
      <Input
        id="zip-members"
        className="font-mono"
        value={draft}
        placeholder={tr('例如 2026*/HEADER*.TXT', 'e.g. 2026*/HEADER*.TXT')}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
        }}
      />
      <p className="text-xs text-muted-foreground">
        {tr(
          'zip 裡的相對路徑，不分大小寫：* 不跨資料夾，** 是任何層數的資料夾（**/HEADER*.TXT 找到 zip 裡任何地方的）。zip 裡每個符合的檔是一個來源。',
          'A path inside the zip, case-insensitive: * stays within one folder, ** matches any number of folders (**/HEADER*.TXT finds it anywhere in the zip). Each matching file is a separate source.',
        )}
      </p>
    </div>
  )
}

/** 掃描結果：每個 zip 裡符合的檔（只讀 zip 的目錄，不解壓）；沒有符合的 zip 黃色提醒，讀不了的紅色。 */
function ZipScanList({ scan }: { scan: ZipScan }) {
  if (scan.error) return <p className="rounded-md bg-destructive/10 p-2 text-xs text-destructive">{scan.error}</p>
  return (
    <div className="space-y-1.5">
      <div className="text-xs text-muted-foreground">{tr('符合的檔（載入前先掃描）', 'Matching files (scanned before loading)')}</div>
      <ul className="divide-y rounded-md border text-xs">
        {scan.zips.map((z) => (
          <li key={z.path} className="space-y-1 px-2 py-1.5">
            <div className="truncate font-mono font-medium" title={z.path}>
              {z.path}
            </div>
            {z.members.length ? (
              <ul className="space-y-0.5">
                {z.members.map((m) => (
                  <li key={m.path} className="flex items-center gap-2">
                    {m.problem ? (
                      <CircleX className="size-3.5 shrink-0 text-destructive" />
                    ) : (
                      <CircleCheck className="size-3.5 shrink-0 text-green-600" />
                    )}
                    <span className="min-w-0 flex-1 truncate font-mono" title={m.problem ?? m.path}>
                      {m.path}
                    </span>
                    <span className="shrink-0 text-muted-foreground tabular-nums">{formatBytes(m.bytes)}</span>
                    <span className={`shrink-0 ${m.problem ? 'text-destructive' : 'text-muted-foreground'}`}>{m.method}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="flex items-center gap-1.5 text-amber-700 dark:text-amber-400">
                <CircleAlert className="size-3.5 shrink-0" />
                {tr('沒有符合的檔（略過，處理紀錄會記下來）', 'No matching files (skipped; recorded in the processing log)')}
              </div>
            )}
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground tabular-nums">
        {tr(
          `共 ${scan.zips.length} 個 zip、${scan.files} 個檔，解開後約 ${formatBytes(scan.bytes)}；暫存大約需要 ${formatBytes(scan.estimate)}${scan.free !== null ? `，磁碟剩 ${formatBytes(scan.free)}` : ''}`,
          `${plural(scan.zips.length, 'zip', 'zips')}, ${plural(scan.files, 'file', 'files')}, about ${formatBytes(scan.bytes)} extracted; needs about ${formatBytes(scan.estimate)} of temp space${scan.free !== null ? ` (${formatBytes(scan.free)} free)` : ''}`,
        )}
      </p>
      {scan.problems.length ? (
        <ul className="space-y-0.5 rounded-md bg-destructive/10 p-2 text-xs text-destructive">
          {scan.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/** 讀前面階段的輸出：勾選要讀的階段（依階段的順序讀）。 */
function StagesInput({ state, onInput }: Pick<InputProps, 'state' | 'onInput'>) {
  const { input } = state
  const picked = new Set(input.stages)
  const toggle = (name: string, on: boolean) => onInput({ stages: input.earlier.filter((n) => (n === name ? on : picked.has(n))) })
  const missing = input.stages.filter((n) => !input.earlier.includes(n))
  /** 讀的階段裡第一個有錯的（有錯時要先修正那個階段，不是重新載入） */
  const broken = state.stages.find((s) => picked.has(s.name) && s.status === 'error')
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <div className="text-xs text-muted-foreground">{tr('要讀的階段（依階段的順序接在一起）', 'Stages to read (in stage order)')}</div>
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
        {missing.length ? (
          <p className="text-xs text-destructive">
            {tr(`不是前面的階段：${missing.join('、')}`, `Not earlier stages: ${missing.join(', ')}`)}
          </p>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {tr(
            '每個來源檔各自是一個來源（名稱是「@階段／檔名」）。要接成一份（附加）就加「合併來源檔」；同名欄的型別要一樣，缺的欄補空值。',
            'Each source file is a separate source (named "@stage/file"). To append them into one, add “Combine source files”; columns with the same name must have the same type, and missing columns are filled with nulls.',
          )}
        </p>
      </div>
      {input.error ? <p className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{input.error}</p> : null}
      {!input.loaded && !input.error ? (
        <p className={`text-xs ${broken ? 'text-destructive' : 'text-muted-foreground'}`}>
          {!input.stages.length
            ? tr('先勾選要讀的階段', 'Select the stages to read first')
            : broken
              ? tr(`讀的「${broken.name}」有錯，先修正那個階段`, `"${broken.name}" has errors; fix that stage first`)
              : tr(
                  '前面的階段還沒有結果：按工具列的「載入資料及預覽」',
                  'Earlier stages have no results yet: click “Load & preview” in the toolbar',
                )}
        </p>
      ) : input.loaded ? (
        <p className="text-xs text-muted-foreground">
          {tr(
            `${input.rows?.toLocaleString() ?? ''} 列 × ${input.columns?.length ?? ''} 欄`,
            `${plural(input.rows ?? 0, 'row', 'rows')} × ${plural(input.columns?.length ?? 0, 'column', 'columns')}`,
          )}
        </p>
      ) : null}
    </div>
  )
}

export function InputPanel({ state, onAddFiles, onAddEntry, onRemove, onInput }: InputProps) {
  const { input } = state
  const [entry, setEntry] = useState('')
  const readerSelect = (
    <div className="space-y-1.5">
      <div className="text-xs text-muted-foreground">{tr('讀取方式', 'Read as')}</div>
      <Select value={input.reader} onValueChange={(reader) => onInput({ reader: reader as ViewState['input']['reader'] })}>
        <SelectTrigger className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.entries(readers()).map(([k, v]) => (
            <SelectItem key={k} value={k} disabled={k === 'stages' && state.current === 0}>
              {v}
              {k === 'stages' && state.current === 0 ? tr('（第一個階段不能選）', ' (not available in the first stage)') : ''}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
  if (input.reader === 'stages') {
    return (
      <section className="space-y-3">
        <div className="text-sm font-medium">{tr('讀取資料', 'Read data')}</div>
        {readerSelect}
        <StagesInput state={state} onInput={onInput} />
      </section>
    )
  }
  const loadHint = !input.files.length
    ? tr('先加入檔案', 'Add files first')
    : !input.can_load
      ? tr('檔案清單有找不到的檔，請先移除或修正', "Some files in the list can't be found; remove or fix them first")
      : input.stale
        ? tr(
            '檔案清單、讀取方式或編碼已變更：按工具列的「載入資料及預覽」重新讀取',
            'The file list, “Read as”, or encoding changed: click “Load & preview” in the toolbar to read again',
          )
        : tr(
            '還沒載入：按工具列的「載入資料及預覽」看每一步的結果',
            'Not loaded yet: click “Load & preview” in the toolbar to see the result of each step',
          )
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
        <div className="text-sm font-medium">{tr('讀取資料', 'Read data')}</div>
        <Button size="sm" variant="outline" onClick={onAddFiles}>
          <FolderOpen /> {tr('瀏覽檔案…', 'Browse files…')}
        </Button>
      </div>
      <div className="space-y-1.5">
        <div className="text-xs text-muted-foreground">{tr('檔案清單', 'Files')}</div>
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
                  <span className="shrink-0 text-xs text-destructive">{tr('找不到', 'Not found')}</span>
                ) : isGlob(e.path) ? (
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {tr(`符合 ${e.found} 個檔`, `Matches ${plural(e.found, 'file', 'files')}`)}
                  </span>
                ) : null}
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={tr(`從清單移除 ${e.path}`, `Remove ${e.path} from list`)}
                  title={tr('從清單移除', 'Remove from list')}
                  onClick={() => onRemove(i)}
                >
                  <X />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="rounded-md border border-dashed p-3 text-center text-xs text-muted-foreground">
            {tr(
              '還沒有檔案。按「瀏覽檔案…」選檔，或在下面輸入路徑。',
              'No files yet. Click “Browse files…” to pick files, or enter a path below.',
            )}
          </p>
        )}
        <form
          className="flex gap-1.5"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <Input
            value={entry}
            onChange={(e) => setEntry(e.target.value)}
            placeholder={
              input.reader === 'zip_lines'
                ? tr('手動輸入 zip 的路徑，例如 data/2026*.zip', 'Enter a zip path, e.g. data/2026*.zip')
                : tr('手動輸入路徑，例如 data/明細_*.csv', 'Enter a path, e.g. data/detail_*.csv')
            }
          />
          <Button type="submit" size="sm" variant="outline" disabled={!entry.trim()}>
            <Plus /> {tr('加入', 'Add')}
          </Button>
        </form>
      </div>
      <div className="space-y-3">
        {readerSelect}
        {input.reader === 'zip_lines' ? <MembersField value={input.members ?? ''} onChange={(members) => onInput({ members })} /> : null}
        {input.reader === 'parquet' ? (
          <p className="text-xs text-muted-foreground">
            {tr(
              '欄位與型別照檔案；檔案自己帶的 __ 開頭欄位改名成「原始…」保留（例如 __line_no → 原始行號）。',
              'Columns and types come from the file; columns in the file that start with __ are kept, renamed to "原始…" (e.g. __line_no → 原始行號).', // i18n-ignore：「原始…」是 Python 產生的欄名（資料），不翻
            )}
          </p>
        ) : input.reader === 'excel' ? (
          <div className="space-y-1.5">
            <div className="text-xs text-muted-foreground">{tr('工作表', 'Sheet')}</div>
            <Select value={input.sheet || FIRST_SHEET} onValueChange={(sheet) => onInput({ sheet: sheet === FIRST_SHEET ? '' : sheet })}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={FIRST_SHEET}>{tr('第一個工作表', 'First sheet')}</SelectItem>
                <SelectItem value={ALL_SHEETS}>
                  {tr('全部工作表（每個檔的每個工作表）', 'All sheets (every sheet in every file)')}
                </SelectItem>
                {[...new Set([...input.sheets, ...(input.sheet && input.sheet !== ALL_SHEETS ? [input.sheet] : [])])].map((name) => (
                  <SelectItem key={name} value={name}>
                    {input.sheets.includes(name) ? name : tr(`${name}（找不到）`, `${name} (not found)`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {tr(
                '欄名是 A、B、C…，行號是 Excel 的列號；每一格都讀成文字。',
                'Columns are named A, B, C…; line numbers are Excel row numbers; every cell is read as text.',
              )}
              {input.sheet === ALL_SHEETS
                ? tr(
                    '每個工作表各自處理，系統欄位 __sheet 記工作表名稱。',
                    ' Each sheet is processed separately; the system column __sheet holds the sheet name.',
                  )
                : ''}
            </p>
          </div>
        ) : (
          <div className="space-y-1.5">
            <div className="text-xs text-muted-foreground">{tr('編碼', 'Encoding')}</div>
            <Select value={input.encoding} onValueChange={(encoding) => onInput({ encoding })}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(encodings()).map(([k, v]) => (
                  <SelectItem key={k} value={k}>
                    {v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>
      {input.reader === 'zip_lines' && input.zip_scan ? <ZipScanList scan={input.zip_scan} /> : null}
      {input.error ? <p className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{input.error}</p> : null}
      {/* 載入資料在工具列（依序載入全部階段）；這裡只寫狀態 */}
      {!input.loaded ? (
        <p className={`text-xs ${hintTone}`}>{loadHint}</p>
      ) : (
        <div className="space-y-1.5">
          <div className="text-xs text-muted-foreground">{tr('已載入的檔案', 'Loaded files')}</div>
          <ul className="divide-y rounded-md border text-xs">
            {input.sources.map((f) => (
              <li
                key={`${f.path}／${f.sheet ?? ''}` /* i18n-ignore：React 的 key，不是畫面文字 */}
                className="flex items-center justify-between gap-2 px-2 py-1.5"
              >
                <span className="min-w-0 truncate" title={f.sheet ? tr(`${f.path}［${f.sheet}］`, `${f.path} [${f.sheet}]`) : f.path}>
                  {f.path}
                  {f.sheet ? <span className="text-muted-foreground">{tr(`［${f.sheet}］`, ` [${f.sheet}]`)}</span> : null}
                </span>
                <span className="shrink-0 text-muted-foreground tabular-nums">
                  {tr(
                    `${f.encoding}・${f.rows.toLocaleString()} ${f.encoding === 'excel' || f.encoding === 'parquet' ? '列' : '行'}・${formatBytes(f.bytes)}`,
                    `${f.encoding} · ${f.encoding === 'excel' || f.encoding === 'parquet' ? plural(f.rows, 'row', 'rows') : plural(f.rows, 'line', 'lines')} · ${formatBytes(f.bytes)}`,
                  )}
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
  onStageOutput: (index: number, file: string, format?: OutputFormat) => void
}

const EXTENSIONS: Record<OutputFormat, string> = { parquet: '.parquet', csv: '.csv' }
/**
 * 去掉跟格式一樣的副檔名（檔名欄只填名稱）。另一種格式的副檔名留著（格式是 Parquet 時填了 a.csv，
 * 輸入框照樣顯示 a.csv、下面寫副檔名不合；改成 a 才會送出）。
 */
const stemOf = (file: string, format: OutputFormat | '' | undefined) =>
  format && file.toLowerCase().endsWith(EXTENSIONS[format]) ? file.slice(0, -EXTENSIONS[format].length) : file

/** 輸出檔名：只填檔名，後面選格式（.parquet／.csv）；檔名離開欄位或按 Enter 才送出，格式選了馬上送（檢查在 Python 做）。 */
function OutputFileField({ stage, onChange }: { stage: StageState; onChange: (file: string, format?: OutputFormat) => void }) {
  const planned = stage.output
  const format = stage.output_format || undefined
  const saved = stemOf(stage.output_file, format)
  const [draft, setDraft] = useState(saved)
  const commit = () => {
    if (draft.trim() !== saved) onChange(draft.trim())
  }
  return (
    <div className="space-y-1.5">
      <label className="text-sm" htmlFor={`output-file-${stage.index}`}>
        {tr('輸出檔名', 'Output file name')}
        {stage.last ? '' : tr('（選填）', ' (optional)')}
      </label>
      <div className="flex items-center gap-1.5">
        <Input
          id={`output-file-${stage.index}`}
          className="h-8"
          value={draft}
          placeholder={stage.last && planned ? stemOf(planned.file, planned.format) : tr('不寫檔', 'No file')}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.currentTarget.blur()
          }}
        />
        <Select value={format} onValueChange={(v) => onChange(draft.trim(), v as OutputFormat)}>
          <SelectTrigger className="h-8 w-28 shrink-0" aria-label={tr('輸出格式', 'Output format')}>
            <SelectValue placeholder={tr('格式', 'Format')} />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(EXTENSIONS) as OutputFormat[]).map((f) => (
              <SelectItem key={f} value={f}>
                {EXTENSIONS[f]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {format === 'csv' ? (
        <p className="text-xs text-muted-foreground">
          {tr(
            'CSV 用 UTF-8（含 BOM），Excel 可以直接打開；匯出後不提供完整模式（「輸出資料」照舊是抽樣模式）。',
            'CSV is UTF-8 (with BOM) and opens directly in Excel; there is no full mode after export (“Output” stays in sample mode).',
          )}
        </p>
      ) : null}
      {planned?.error ? (
        <p className="text-xs text-destructive">{planned.error}</p>
      ) : planned ? (
        <p className="text-xs break-all text-muted-foreground">
          {tr(`寫到 ${planned.path}`, `Writes to ${planned.path}`)}
          {planned.default ? tr('（沒填時用 Recipe 的名稱）', ' (uses the Recipe name when left blank)') : ''}
          {planned.exists ? (
            <span className="text-amber-700 dark:text-amber-400">
              {tr('；已經有這個檔，匯出時會覆蓋', '; this file already exists and will be overwritten on export')}
            </span>
          ) : null}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          {tr('填了才另外存成檔案（存到 output 資料夾）。', 'Fill this in to also save a file (in the output folder).')}
        </p>
      )}
    </div>
  )
}

export function OutputPanel({ state, onOutput, onStageOutput }: OutputProps) {
  const { output } = state
  const stage = state.stages[state.current]
  // 換階段或 Python 改了檔名（例如拿掉和格式一樣的副檔名）時重新開始編輯
  const field = stage ? (
    <OutputFileField
      key={`${stage.index}:${stage.output_file}`}
      stage={stage}
      onChange={(file, format) => onStageOutput(stage.index, file, format)}
    />
  ) : null
  if (stage && !stage.last) {
    const users = state.stages.filter((s) => s.uses.includes(stage.name)).map((s) => tr(`「${s.name}」`, `"${s.name}"`))
    return (
      <section className="space-y-3">
        <div className="text-sm font-medium">{tr('階段結果', 'Stage result')}</div>
        <p className="text-sm text-muted-foreground">
          {tr(`後面的階段用 @${stage.name} 讀這個階段的結果`, `Later stages read this stage's result as @${stage.name}`)}
          {users.length
            ? tr(`（目前${users.join('、')}用到它）`, ` (used by ${users.join(', ')})`)
            : tr('（目前還沒有階段用到它）', ' (not used by any stage yet)')}
          {tr('；填了輸出檔名也會另外存成檔案。', '; fill in an output file name to also save it to a file.')}
        </p>
        {field}
      </section>
    )
  }
  return (
    <section className="space-y-3">
      <div className="text-sm font-medium">{tr('輸出資料', 'Output')}</div>
      <p className="text-xs text-muted-foreground">
        {tr(
          '按「處理並匯出資料」處理全部資料，存到 output 資料夾。',
          'Click “Process & export” to process all data and save it to the output folder.',
        )}
      </p>
      {field}
      <label className="flex items-center justify-between gap-3 text-sm">
        <span>
          {tr('保留系統欄位（__source_file、__line_no 等）', 'Keep system columns (__source_file, __line_no, etc.)')}
          <span className="block text-xs text-muted-foreground">{tr('套用到每個輸出檔', 'Applies to every output file')}</span>
        </span>
        <Switch checked={output.keep_system_columns} onCheckedChange={(keep_system_columns) => onOutput({ keep_system_columns })} />
      </label>
      <label className="flex items-start justify-between gap-3 text-sm">
        <span>
          {tr('同時寫出處理紀錄（manifest）', 'Also write a processing log (manifest)')}
          <span className="block text-xs text-muted-foreground">
            {tr(
              '每個輸出檔在 log 資料夾各寫一份 .manifest.json：用了哪份 Recipe、輸入與輸出檔的 SHA-256、每一步的列數與警告',
              'Writes a .manifest.json per output file to the log folder: the Recipe used, SHA-256 of input and output files, and row counts and warnings for each step',
            )}
          </span>
        </span>
        <Switch checked={output.manifest} onCheckedChange={(manifest) => onOutput({ manifest })} />
      </label>
    </section>
  )
}
