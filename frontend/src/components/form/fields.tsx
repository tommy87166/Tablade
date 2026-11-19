/** 表單欄位元件：依 JSON Schema 的型別與 ui 提示挑選（DESIGN.md §16.5）。 */
import { ArrowDown, ArrowUp, ArrowUpRight, ChevronUp, Pencil, Play, Plus, RefreshCw, X } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { plural, tr } from '@/i18n'
import type { Issue, JsonSchema } from '../../bridge'
import { ExpressionField } from './ExpressionField'
import { OptionIcon } from './OptionIcon'
import { defaultFor, fieldKind, issuesAt, located, resolve, variants, type FieldKind } from '../../lib/schema'

/** 前面的階段（結合的右表選單、右表的欄位）。 */
export interface FormStage {
  index: number
  name: string
  /** 這個階段結果的資料欄；還算不出來時是 null */
  columns: string[] | null
  sampled: boolean
  full: boolean
}

/** 表單用到、但不在步驟參數裡的資訊。 */
export interface FormContext {
  /** 目前階段前面的階段 */
  stages: FormStage[]
  /** 這一步目前的參數（右表的欄位要看選了哪個階段） */
  params: Record<string, unknown>
  /** 「前往」：切到那個階段、選它的結果 */
  onGoStage?: (index: number) => void
  /** 「完整跑一次」：用全部資料算出那個階段（只跑它和它用到的階段） */
  onFullRun?: (index: number) => void
  /** 「重新從資料讀取」：這個參數的值從資料重新讀出（參數名稱） */
  onRefresh?: (field: string) => void
}

export interface FieldProps {
  name: string
  path: string
  schema: JsonSchema
  root: JsonSchema
  value: unknown
  onChange: (value: unknown) => void
  columns: string[] | null
  /** 這一步輸入的系統欄位（自訂的運算式可以讀） */
  systemColumns?: string[] | null
  /** 這一步之前的來源檔（來源檔順序用） */
  files?: string[] | null
  issues: Issue[]
  required: boolean
  context?: FormContext
}

export function FieldErrors({ issues }: { issues: Issue[] }) {
  if (!issues.length) return null
  return (
    <ul className="mt-1 space-y-0.5 text-xs text-destructive">
      {issues.map((i) => (
        <li key={`${i.path}:${i.error}`}>
          {i.error}
          {i.hint ? tr(`（${i.hint}）`, ` (${i.hint})`) : ''}
        </li>
      ))}
    </ul>
  )
}

/** 清單型的欄位（條件、物件的陣列）自己在每一項底下顯示的錯誤：路徑是 path[i]…，i 是現有的項目 */
function shownInItems(kind: FieldKind, issue: Issue, path: string, value: unknown): boolean {
  if (kind !== 'conditions' && kind !== 'objects') return false
  const m = /^\[(\d+)\]/.exec(issue.path.slice(path.length))
  return m !== null && Array.isArray(value) && Number(m[1]) < value.length
}

/** 換條件時沿用的值轉成新欄位的型別（文字 "100" 換成數字條件時變成 100）；轉不了就不沿用（回到預設值）。 */
function coerce(value: unknown, prop: JsonSchema, root: JsonSchema): unknown {
  if (value === undefined || value === null) return undefined
  const kind = fieldKind(prop, root)
  switch (kind) {
    case 'number':
    case 'integer': {
      const text = typeof value === 'string' ? value.trim() : ''
      const n = typeof value === 'number' ? value : text !== '' ? Number(text) : Number.NaN
      if (!Number.isFinite(n) || (kind === 'integer' && !Number.isInteger(n))) return undefined
      return n
    }
    case 'string':
    case 'regex':
    case 'column':
      return typeof value === 'string' ? value : typeof value === 'number' || typeof value === 'boolean' ? String(value) : undefined
    case 'boolean':
      return typeof value === 'boolean' ? value : undefined
    default:
      return value
  }
}

/** 物件 schema 裡某個參數的標題（錯誤訊息寫位置用） */
function titleOf(schema: JsonSchema, name: string, root: JsonSchema): string | undefined {
  const prop = schema.properties?.[name]
  return prop ? resolve(prop, root).title : undefined
}

function asString(value: unknown): string {
  return value === undefined || value === null ? '' : String(value)
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(asString) : []
}

function TextInput({
  value,
  onChange,
  schema,
  placeholder,
}: {
  value: unknown
  onChange: (v: unknown) => void
  schema: JsonSchema
  placeholder?: string
}) {
  return (
    <Input
      value={asString(value)}
      maxLength={schema.maxLength}
      placeholder={placeholder}
      className={schema.maxLength === 1 ? 'w-16 text-center font-mono' : undefined}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

function NumberInput({ value, onChange, integer }: { value: unknown; onChange: (v: unknown) => void; integer: boolean }) {
  return (
    <Input
      type="number"
      step={integer ? 1 : 'any'}
      value={value === undefined || value === null ? '' : String(value)}
      className="w-32 tabular-nums"
      onChange={(e) => {
        const raw = e.target.value
        if (raw === '') return onChange(undefined)
        const n = integer ? Number.parseInt(raw, 10) : Number(raw)
        onChange(Number.isNaN(n) ? raw : n)
      }}
    />
  )
}

function EnumSelect({ value, onChange, schema }: { value: unknown; onChange: (v: unknown) => void; schema: JsonSchema }) {
  const labels = schema.ui_labels ?? {}
  const select = (
    <Select value={asString(value)} onValueChange={onChange}>
      <SelectTrigger className="w-full">
        <SelectValue placeholder={tr('請選擇', 'Select…')} />
      </SelectTrigger>
      <SelectContent>
        {(schema.enum ?? []).map((option) => (
          <SelectItem key={String(option)} value={String(option)}>
            {labels[String(option)] ?? String(option)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
  // 選到的選項有說明時（例如日期樣式的正規表示式）顯示在下面
  const hint = schema.ui_option_hints?.[asString(value)]
  if (!hint) return select
  return (
    <div className="space-y-1">
      {select}
      <p className="break-all font-mono text-xs text-muted-foreground">{hint}</p>
    </div>
  )
}

/** 選項卡片（例如結合的方式）：每個選項一張卡片，上面是小圖、標題、說明，兩欄排列。 */
function OptionCards({ value, onChange, schema }: { value: unknown; onChange: (v: unknown) => void; schema: JsonSchema }) {
  const current = asString(value)
  const labels = schema.ui_labels ?? {}
  const hints = schema.ui_option_hints ?? {}
  const icons = schema.ui_option_icons ?? {}
  return (
    <div role="radiogroup" className="grid grid-cols-2 gap-2">
      {(schema.enum ?? []).map((raw) => {
        const option = String(raw)
        const selected = option === current
        return (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option)}
            className={`flex flex-col items-center gap-1 rounded-md border p-2 text-center ${
              selected ? 'border-primary bg-primary/5 ring-1 ring-primary/40' : 'hover:bg-muted'
            }`}
          >
            <OptionIcon name={icons[option]} />
            <span className="text-sm font-medium">{labels[option] ?? option}</span>
            {hints[option] ? <span className="self-stretch text-left text-xs text-muted-foreground">{hints[option]}</span> : null}
          </button>
        )
      })}
    </div>
  )
}

/** ui 是 own_name：選項是這一步自己的另一個參數（ui_from，例如拆出來的欄名）目前的值。 */
function ownNames({ schema, root, context }: FieldProps): string[] {
  const from = resolve(schema, root).ui_from
  const values = from ? context?.params[from] : undefined
  return Array.isArray(values) ? values.filter((v): v is string => typeof v === 'string' && v !== '') : []
}

function ColumnSelect({ value, onChange, columns }: { value: unknown; onChange: (v: unknown) => void; columns: string[] | null }) {
  const current = asString(value)
  if (!columns) return <Input value={current} placeholder={tr('欄位名稱', 'Column name')} onChange={(e) => onChange(e.target.value)} />
  const options = columns.includes(current) || !current ? columns : [current, ...columns]
  return (
    <Select value={current} onValueChange={onChange}>
      <SelectTrigger className="w-full">
        <SelectValue placeholder={tr('選擇欄位', 'Choose a column')} />
      </SelectTrigger>
      <SelectContent>
        {options.map((c) => (
          <SelectItem key={c} value={c}>
            {columns.includes(c) ? c : tr(`${c}（不存在）`, `${c} (missing)`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/**
 * 勾選欄位：存的順序是勾的順序（取消樞紐、群組依據的順序有意義），勾選時加到最後；
 * 選了兩欄以上時，欄名旁邊的小數字是第幾個。資料裡沒有的欄名留著（紅字），按 × 才移除。
 */
function ColumnChecklist({ value, onChange, columns }: { value: unknown; onChange: (v: unknown) => void; columns: string[] | null }) {
  const selected = asList(value)
  if (!columns) return <TagsEditor value={selected} onChange={onChange} placeholder={tr('欄位名稱', 'Column name')} />
  const toggle = (name: string, on: boolean) =>
    onChange(on ? [...selected.filter((c) => c !== name), name] : selected.filter((c) => c !== name))
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 rounded-md border p-2">
      {columns.map((c) => (
        <label key={c} className="flex items-center gap-1.5 text-sm">
          <Checkbox checked={selected.includes(c)} onCheckedChange={(on) => toggle(c, on === true)} />
          {c}
          {selected.length > 1 && selected.includes(c) ? (
            <span className="text-xs text-muted-foreground tabular-nums">{selected.indexOf(c) + 1}</span>
          ) : null}
        </label>
      ))}
      {selected
        .filter((c) => !columns.includes(c))
        .map((c) => (
          <span key={c} className="flex items-center gap-0.5 text-sm text-destructive">
            {tr(`${c}（不存在）`, `${c} (missing)`)}
            <Button variant="ghost" size="icon-xs" aria-label={tr(`移除 ${c}`, `Remove ${c}`)} onClick={() => toggle(c, false)}>
              <X />
            </Button>
          </span>
        ))}
    </div>
  )
}

/** 參數裡選的右表（schema 標了 ui: stage 的參數）是哪個階段。 */
function rightStage(root: JsonSchema, context?: FormContext): FormStage | undefined {
  if (!context) return undefined
  const field = Object.entries(root.properties ?? {}).find(([, prop]) => resolve(prop, root).ui === 'stage')?.[0]
  const name = field ? context.params[field] : undefined
  return context.stages.find((s) => s.name === name)
}

/** 選前面的階段（結合的右表）；下面可以「前往」那個階段，右表是樣本時可以「完整跑一次」。 */
function StageSelect({ value, onChange, context }: { value: unknown; onChange: (v: unknown) => void; context?: FormContext }) {
  const current = asString(value)
  const stages = context?.stages ?? []
  if (!stages.length)
    return (
      <p className="text-xs text-muted-foreground">
        {tr(
          '前面沒有階段：先在最左邊新增一個階段（例如對照表）。',
          'No earlier stages: add a stage on the far left first (for example, a lookup table).',
        )}
      </p>
    )
  const names = stages.map((s) => s.name)
  const options = names.includes(current) || !current ? names : [current, ...names]
  const picked = stages.find((s) => s.name === current)
  return (
    <div className="space-y-1.5">
      <Select value={current} onValueChange={onChange}>
        <SelectTrigger className="w-full">
          <SelectValue placeholder={tr('選擇前面的階段', 'Choose an earlier stage')} />
        </SelectTrigger>
        <SelectContent>
          {options.map((n) => (
            <SelectItem key={n} value={n}>
              @{n}
              {names.includes(n) ? '' : tr('（不是前面的階段）', ' (not an earlier stage)')}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {picked ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <Button variant="outline" size="xs" onClick={() => context?.onGoStage?.(picked.index)}>
            <ArrowUpRight /> {tr(`前往 @${picked.name}`, `Go to @${picked.name}`)}
          </Button>
          {picked.sampled ? (
            <Button variant="outline" size="xs" onClick={() => context?.onFullRun?.(picked.index)}>
              <Play /> {tr('完整跑一次', 'Run in full')}
            </Button>
          ) : null}
          {picked.full ? <span className="text-xs text-muted-foreground">{tr('用完整的結果', 'Using the full result')}</span> : null}
        </div>
      ) : null}
      {picked?.sampled ? (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          {tr(
            '另一張表是樣本：預覽時有些列可能對不到，匯出時會用完整的資料。按「完整跑一次」用全部資料算出另一張表。',
            'The other table is a sample: some rows may not match in the preview, but export uses the full data. Click “Run in full” to compute the other table from all data.',
          )}
        </p>
      ) : null}
    </div>
  )
}

/**
 * 從資料讀出的值（例如樞紐要變成欄的值）：平常收起來只顯示一行，旁邊「重新從資料讀取」「修改」。
 * 依據的欄（schema 的 ui_from）在表單上改了，Python 會自動重新讀。
 */
function DataValues({ name, value, onChange, schema, root, context }: FieldProps) {
  const [editing, setEditing] = useState(false)
  const items = asList(value)
  const sourceField = resolve(schema, root).ui_from
  const sourceProp = sourceField ? root.properties?.[sourceField] : undefined
  const sourceTitle = sourceProp ? (resolve(sourceProp, root).title ?? sourceField) : ''
  const column = sourceField ? asString(context?.params[sourceField]) : ''
  return (
    <div className="space-y-1.5 rounded-md border p-2">
      {editing ? (
        <TagsEditor value={items} onChange={onChange} placeholder={tr('值', 'Value')} />
      ) : (
        <p className="text-sm break-all">
          {items.length ? (
            <>
              <span className="text-muted-foreground tabular-nums">
                {tr(`${items.length} 個：`, `${plural(items.length, 'value', 'values')}: `)}
              </span>
              {items.join(tr('、', ', '))}
            </>
          ) : (
            <span className="text-muted-foreground">{tr('還沒有', 'None yet')}</span>
          )}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <Button variant="outline" size="xs" disabled={!column || !context?.onRefresh} onClick={() => context?.onRefresh?.(name)}>
          <RefreshCw /> {tr('重新從資料讀取', 'Reload from data')}
        </Button>
        <Button variant="ghost" size="xs" onClick={() => setEditing(!editing)}>
          {editing ? <ChevronUp /> : <Pencil />} {editing ? tr('收起', 'Collapse') : tr('修改', 'Edit')}
        </Button>
      </div>
      {column ? (
        <p className="text-xs text-muted-foreground">
          {tr(
            `從「${column}」${sourceTitle ? `（${sourceTitle}）` : ''}讀出；改了依據的欄會自動重新讀。`,
            `Read from "${column}"${sourceTitle ? ` (${sourceTitle})` : ''}; re-read automatically when that column changes.`,
          )}
        </p>
      ) : null}
    </div>
  )
}

/** 依位置選欄：外觀和 ColumnChecklist 一樣，存的是第幾個資料欄（從 1 起）。 */
function PositionChecklist({ value, onChange, columns }: { value: unknown; onChange: (v: unknown) => void; columns: string[] | null }) {
  const selected = Array.isArray(value) ? value.filter((v): v is number => typeof v === 'number') : []
  // 沒有欄位清單時用文字輸入；打到一半的「2,」不會被改回「2」
  if (!columns) return <IntListInput value={value} onChange={onChange} placeholder={tr('例如 2, 5', 'e.g. 2, 5')} />
  const toggle = (position: number, on: boolean) =>
    onChange(on ? [...selected.filter((p) => p !== position), position].sort((a, b) => a - b) : selected.filter((p) => p !== position))
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 rounded-md border p-2">
      {columns.map((c, i) => (
        <label key={c} className="flex items-center gap-1.5 text-sm">
          <Checkbox checked={selected.includes(i + 1)} onCheckedChange={(on) => toggle(i + 1, on === true)} />
          <span className="tabular-nums text-muted-foreground">{i + 1}</span>
          {c}
        </label>
      ))}
      {selected
        .filter((p) => p > columns.length)
        .map((p) => (
          <span key={p} className="text-sm text-destructive">
            {tr(`第 ${p} 欄（不存在）`, `Column ${p} (missing)`)}
          </span>
        ))}
    </div>
  )
}

/** 整數清單（例如依固定位置拆欄的開始位置）：用逗號或空白分開，例如 1, 12, 18。 */
function IntListInput({ value, onChange, placeholder }: { value: unknown; onChange: (v: unknown) => void; placeholder?: string }) {
  const numbers = Array.isArray(value) ? value.filter((v): v is number => typeof v === 'number') : []
  const serverText = numbers.join(', ')
  const [text, setText] = useState(serverText)
  const [synced, setSynced] = useState(serverText)
  // 外面（建議值、復原）改了才同步；正在輸入「1, 1」這種還沒打完的值時不覆蓋（在 render 時調整，不用 effect）
  if (serverText !== synced) {
    setSynced(serverText)
    if (parseInts(text).join(', ') !== serverText) setText(serverText)
  }
  return (
    <Input
      value={text}
      placeholder={placeholder ?? tr('例如 1, 12, 18', 'e.g. 1, 12, 18')}
      className="font-mono tabular-nums"
      onChange={(e) => {
        setText(e.target.value)
        onChange(parseInts(e.target.value))
      }}
    />
  )
}

function parseInts(text: string): number[] {
  return text
    .split(/[,，\s]+/) // i18n-ignore
    .map((t) => Number.parseInt(t, 10))
    .filter((n) => Number.isInteger(n))
}

/** 保留和排序欄位：上面是要保留的欄（依順序，可以上下移、移除），下面是沒保留的欄（勾選就加到最後）。 */
function ColumnOrderEditor({ value, onChange, columns }: { value: unknown; onChange: (v: unknown) => void; columns: string[] | null }) {
  const selected = asList(value)
  if (!columns) return <TagsEditor value={selected} onChange={onChange} placeholder={tr('欄位名稱（依順序）', 'Column names (in order)')} />
  const rest = columns.filter((c) => !selected.includes(c))
  const move = (i: number, to: number) => {
    const next = [...selected]
    const [item] = next.splice(i, 1)
    next.splice(to, 0, item)
    onChange(next)
  }
  return (
    <div className="space-y-2">
      <ol className="divide-y rounded-md border text-sm">
        {selected.map((c, i) => (
          <li key={c} className="flex items-center gap-1.5 py-1 pr-1 pl-2">
            <span className="w-5 text-right text-xs text-muted-foreground tabular-nums">{i + 1}</span>
            <span className={`min-w-0 flex-1 truncate ${columns.includes(c) ? '' : 'text-destructive'}`}>
              {columns.includes(c) ? c : tr(`${c}（不存在）`, `${c} (missing)`)}
            </span>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={tr(`上移 ${c}`, `Move ${c} up`)}
              disabled={i === 0}
              onClick={() => move(i, i - 1)}
            >
              <ArrowUp />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={tr(`下移 ${c}`, `Move ${c} down`)}
              disabled={i === selected.length - 1}
              onClick={() => move(i, i + 1)}
            >
              <ArrowDown />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={tr(`不保留 ${c}`, `Don't keep ${c}`)}
              onClick={() => onChange(selected.filter((x) => x !== c))}
            >
              <X />
            </Button>
          </li>
        ))}
        {selected.length === 0 ? (
          <li className="p-2 text-xs text-muted-foreground">{tr('還沒有選要保留的欄位', 'No columns selected to keep yet')}</li>
        ) : null}
      </ol>
      {rest.length ? (
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{tr('不保留的欄位（勾選就加到最後）', 'Columns not kept (check to add to the end)')}</span>
            <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => onChange([...selected, ...rest])}>
              {tr('全部加入', 'Add all')}
            </Button>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5 rounded-md border border-dashed p-2">
            {rest.map((c) => (
              <label key={c} className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Checkbox checked={false} onCheckedChange={() => onChange([...selected, c])} />
                {c}
              </label>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

/** 改欄名：每個欄位一列，右邊填新名稱（空白代表不改）；只存有填的。 */
function RenameMapEditor({ value, onChange, columns }: { value: unknown; onChange: (v: unknown) => void; columns: string[] | null }) {
  const raw = value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
  const names: Record<string, string> = Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, asString(v)]))
  const set = (old: string, next: string) => {
    const out = { ...names }
    if (next) out[old] = next
    else delete out[old]
    onChange(out)
  }
  const missing = Object.keys(names).filter((k) => !columns?.includes(k))
  return (
    <div className="space-y-1.5">
      {columns ? (
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] items-center gap-x-2 gap-y-1.5 rounded-md border p-2">
          {columns.map((c) => (
            <div key={c} className="contents">
              <span className="truncate text-sm" title={c}>
                {c}
              </span>
              <Input
                value={names[c] ?? ''}
                placeholder={tr('不改', 'No change')}
                className="h-8"
                onChange={(e) => set(c, e.target.value)}
              />
            </div>
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          {tr('載入資料後，這裡會列出每一個欄位。', 'Every column will be listed here once data is loaded.')}
        </p>
      )}
      {missing.map((k) => (
        <div key={k} className="flex items-center gap-2 text-sm text-destructive">
          <span className="min-w-0 flex-1 truncate">{tr(`${k}（不存在）→ ${names[k]}`, `${k} (missing) → ${names[k]}`)}</span>
          <Button variant="ghost" size="icon-xs" aria-label={tr(`移除 ${k}`, `Remove ${k}`)} onClick={() => set(k, '')}>
            <X />
          </Button>
        </div>
      ))}
    </div>
  )
}

/** 物件的陣列（例如取代文字的「找 → 換成」）：每一項一個框，依子 schema 畫欄位，可以新增、移除。 */
function ObjectListEditor(props: FieldProps) {
  const { value, onChange, schema, root, path, issues } = props
  const itemSchema = resolve(resolve(schema, root).items ?? {}, root)
  const items = (Array.isArray(value) ? value : []) as Record<string, unknown>[]
  const fields = Object.entries(itemSchema.properties ?? {})
  const required = itemSchema.required ?? []
  const blank = () => Object.fromEntries(fields.map(([name, prop]) => [name, defaultFor(prop, root) ?? '']))
  const update = (i: number, name: string, v: unknown) => onChange(items.map((item, k) => (k === i ? { ...item, [name]: v } : item)))
  return (
    <div className="space-y-2">
      {items.map((item, i) => (
        <div key={i} className="grid grid-cols-[3rem_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1.5 rounded-md border bg-muted/30 p-2">
          {fields.map(([name, prop], j) => {
            const fieldProps: FieldProps = {
              ...props,
              name,
              path: `${path}[${i}].${name}`,
              schema: prop,
              value: name in item ? item[name] : resolve(prop, root).default, // 存檔時省略的預設值也要顯示
              required: required.includes(name),
              onChange: (v) => update(i, name, v),
            }
            const own = issues.filter((x) => x.path === fieldProps.path)
            return (
              <div key={name} className="contents">
                <span className="text-xs text-muted-foreground">{resolve(prop, root).title ?? name}</span>
                <div className={j === 0 ? '' : 'col-span-2'}>
                  {control(fieldKind(prop, root), fieldProps)}
                  <FieldErrors issues={own} />
                </div>
                {j === 0 ? (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={tr('移除這一組', 'Remove this item')}
                    onClick={() => onChange(items.filter((_, k) => k !== i))}
                  >
                    <X />
                  </Button>
                ) : null}
              </div>
            )
          })}
          {/* 這一項其他的錯誤（例如多了不認得的參數）：上面的欄位沒有顯示的都列在這裡 */}
          <div className="col-span-3 empty:hidden">
            <FieldErrors
              issues={issuesAt(issues, `${path}[${i}]`)
                .filter((x) => !fields.some(([name]) => x.path === `${path}[${i}].${name}`))
                .map((x) => located(x, `${path}[${i}]`, (name) => titleOf(itemSchema, name, root)))}
            />
          </div>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange([...items, blank()])}>
        <Plus /> {tr('新增一組', 'Add item')}
      </Button>
    </div>
  )
}

export function TagsEditor({ value, onChange, placeholder }: { value: unknown; onChange: (v: unknown) => void; placeholder?: string }) {
  const items = asList(value)
  const set = (i: number, text: string) => onChange(items.map((v, k) => (k === i ? text : v)))
  return (
    <div className="space-y-1.5">
      {items.map((item, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <span className="w-5 text-right text-xs text-muted-foreground tabular-nums">{i + 1}</span>
          <Input value={item} placeholder={placeholder} onChange={(e) => set(i, e.target.value)} />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={tr('移除', 'Remove')}
            onClick={() => onChange(items.filter((_, k) => k !== i))}
          >
            <X />
          </Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange([...items, ''])}>
        <Plus /> {tr('新增', 'Add')}
      </Button>
    </div>
  )
}

/** 來源檔順序：列出目前的來源檔，可上下移動；存成完整的順序。 */
function SourceFilesOrder({ value, onChange, files }: { value: unknown; onChange: (v: unknown) => void; files: string[] | null }) {
  const listed = asList(value)
  if (!files) return <TagsEditor value={listed} onChange={onChange} placeholder={tr('來源檔名稱', 'Source file name')} />
  const order = [...listed.filter((f) => files.includes(f)), ...files.filter((f) => !listed.includes(f))]
  const missing = listed.filter((f) => !files.includes(f))
  const move = (i: number, to: number) => {
    const next = [...order]
    const [item] = next.splice(i, 1)
    next.splice(to, 0, item)
    onChange([...next, ...missing])
  }
  return (
    <div className="space-y-1.5">
      <ol className="divide-y rounded-md border text-sm">
        {order.map((f, i) => (
          <li key={f} className="flex items-center gap-1.5 py-1 pr-1 pl-2">
            <span className="w-5 text-right text-xs text-muted-foreground tabular-nums">{i + 1}</span>
            <span className="min-w-0 flex-1 truncate">{f}</span>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={tr(`上移 ${f}`, `Move ${f} up`)}
              disabled={i === 0}
              onClick={() => move(i, i - 1)}
            >
              <ArrowUp />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={tr(`下移 ${f}`, `Move ${f} down`)}
              disabled={i === order.length - 1}
              onClick={() => move(i, i + 1)}
            >
              <ArrowDown />
            </Button>
          </li>
        ))}
      </ol>
      {missing.length ? (
        <p className="text-xs text-muted-foreground">
          {tr(`目前的資料沒有：${missing.join('、')}`, `Not in the current data: ${missing.join(', ')}`)}
        </p>
      ) : null}
      {listed.length ? (
        <Button variant="outline" size="sm" onClick={() => onChange([])}>
          {tr('恢復原本的順序', 'Restore original order')}
        </Button>
      ) : null}
    </div>
  )
}

type Condition = Record<string, unknown> & { op?: string; column?: string }

function ConditionsEditor({ value, onChange, schema, root, columns, issues, path }: FieldProps) {
  const items = (Array.isArray(value) ? value : []) as Condition[]
  const options = variants(schema, root)
  const byOp = new Map(options.map((v) => [v.op, v]))
  const groups = new Map<string, typeof options>()
  for (const v of options) {
    const key = v.schema.title ?? ''
    groups.set(key, [...(groups.get(key) ?? []), v])
  }
  const update = (i: number, next: Condition) => onChange(items.map((c, k) => (k === i ? next : c)))
  const changeOp = (i: number, op: string) => {
    const variant = byOp.get(op)
    const base: Condition = { column: items[i].column, op }
    for (const [name, prop] of Object.entries(variant?.schema.properties ?? {})) {
      if (name in base) continue
      // 沿用舊的值時轉成新條件的型別（「等於」的文字 "100" 換成「≥」時是數字 100），轉不了就清掉
      base[name] = coerce(items[i][name], prop, root) ?? defaultFor(prop, root)
    }
    update(i, base)
  }

  const label = 'text-xs text-muted-foreground'

  return (
    <div className="space-y-2">
      {items.map((cond, i) => {
        const variant = cond.op ? byOp.get(cond.op) : undefined
        const extra = Object.entries(variant?.schema.properties ?? {}).filter(([n]) => n !== 'column' && n !== 'op')
        const required = variant?.schema.required ?? []
        const at = (name: string) => issues.filter((x) => x.path === `${path}[${i}].${name}`)
        return (
          // 欄位、條件、值各佔一行：右欄再窄也看得到選了哪一欄
          <div
            key={i}
            className="grid grid-cols-[2.75rem_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1.5 rounded-md border bg-muted/30 p-2"
          >
            <span className={label}>{tr('欄位', 'Column')}</span>
            <ColumnSelect value={cond.column} columns={columns} onChange={(c) => update(i, { ...cond, column: String(c) })} />
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={tr('移除條件', 'Remove condition')}
              onClick={() => onChange(items.filter((_, k) => k !== i))}
            >
              <X />
            </Button>
            {at('column').length ? (
              <div className="col-span-2 col-start-2">
                <FieldErrors issues={at('column')} />
              </div>
            ) : null}

            <span className={label}>{tr('條件', 'Condition')}</span>
            <div className="col-span-2">
              <Select value={cond.op ?? ''} onValueChange={(op) => changeOp(i, op)}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={tr('選擇條件', 'Choose a condition')} />
                </SelectTrigger>
                <SelectContent>
                  {[...groups.entries()].map(([group, list]) => (
                    <SelectGroup key={group}>
                      <SelectLabel>{group}</SelectLabel>
                      {list.map((v) => (
                        <SelectItem key={v.op} value={v.op}>
                          {v.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {extra.map(([name, prop]) => {
              const kind = fieldKind(prop, root)
              const fieldProps: FieldProps = {
                name,
                path: `${path}[${i}].${name}`,
                schema: prop,
                root,
                value: cond[name],
                columns,
                issues,
                required: required.includes(name),
                onChange: (v) => update(i, { ...cond, [name]: v }),
              }
              const title = resolve(prop, root).title ?? name
              if (kind === 'boolean') {
                return (
                  <label key={name} className="col-span-3 flex items-center justify-between gap-3">
                    <span className={label}>{title}</span>
                    {control(kind, fieldProps)}
                  </label>
                )
              }
              return (
                <div key={name} className="contents">
                  <span className={label}>
                    {title}
                    {fieldProps.required ? <span className="text-destructive">*</span> : null}
                  </span>
                  <div className="col-span-2">{control(kind, fieldProps)}</div>
                  {at(name).length ? (
                    <div className="col-span-2 col-start-2">
                      <FieldErrors issues={at(name)} />
                    </div>
                  ) : null}
                </div>
              )
            })}
            {/* 這個條件其他的錯誤：上面的欄位沒有顯示的都列在這裡 */}
            <div className="col-span-3 empty:hidden">
              <FieldErrors
                issues={issuesAt(issues, `${path}[${i}]`)
                  .filter((x) => !['column', ...extra.map(([n]) => n)].some((n) => x.path === `${path}[${i}].${n}`))
                  .map((x) => located(x, `${path}[${i}]`, (name) => (variant ? titleOf(variant.schema, name, root) : undefined)))}
              />
            </div>
          </div>
        )
      })}
      <Button variant="outline" size="sm" onClick={() => onChange([...items, { column: columns?.[0] ?? '', op: 'equals', value: '' }])}>
        <Plus /> {tr('新增條件', 'Add condition')}
      </Button>
    </div>
  )
}

function control(kind: FieldKind, props: FieldProps) {
  const { value, onChange, columns } = props
  const schema = resolve(props.schema, props.root)
  switch (kind) {
    case 'column':
      return <ColumnSelect value={value} onChange={onChange} columns={columns} />
    case 'columns':
      return <ColumnChecklist value={value} onChange={onChange} columns={columns} />
    case 'column_positions':
      return <PositionChecklist value={value} onChange={onChange} columns={columns} />
    case 'new_columns':
      return <TagsEditor value={value} onChange={onChange} placeholder={tr('欄名', 'Column name')} />
    case 'conditions':
      return <ConditionsEditor {...props} />
    case 'source_files':
      return <SourceFilesOrder value={value} onChange={onChange} files={props.files ?? null} />
    case 'stage':
      return <StageSelect value={value} onChange={onChange} context={props.context} />
    case 'right_column':
      return <ColumnSelect value={value} onChange={onChange} columns={rightStage(props.root, props.context)?.columns ?? null} />
    case 'data_values':
      return <DataValues {...props} />
    case 'option_cards':
      return <OptionCards value={value} onChange={onChange} schema={schema} />
    case 'own_name':
      return <ColumnSelect value={value} onChange={onChange} columns={ownNames(props)} />
    case 'expression':
      return (
        <ExpressionField value={value} onChange={onChange} columns={columns} systemColumns={props.systemColumns ?? null} syntax={schema.ui_syntax ?? []} />
      )
    case 'right_columns':
      return <ColumnChecklist value={value} onChange={onChange} columns={rightStage(props.root, props.context)?.columns ?? null} />
    case 'column_order':
      return <ColumnOrderEditor value={value} onChange={onChange} columns={columns} />
    case 'rename_map':
      return <RenameMapEditor value={value} onChange={onChange} columns={columns} />
    case 'int_list':
      return <IntListInput value={value} onChange={onChange} />
    case 'objects':
      return <ObjectListEditor {...props} />
    case 'enum':
      return <EnumSelect value={value} onChange={onChange} schema={schema} />
    case 'boolean':
      return <Switch checked={value === true} onCheckedChange={onChange} />
    case 'integer':
    case 'number':
      return <NumberInput value={value} onChange={onChange} integer={kind === 'integer'} />
    case 'regex':
      return <TextInput value={value} onChange={onChange} schema={schema} placeholder={tr('例如 ^\\d{3}/', 'e.g. ^\\d{3}/')} />
    default:
      return <TextInput value={value} onChange={onChange} schema={schema} />
  }
}

export function Field(props: FieldProps & { compact?: boolean }) {
  const schema = resolve(props.schema, props.root)
  const kind = fieldKind(props.schema, props.root)
  // 路徑落在這個欄位底下的錯誤都顯示（例如新欄名清單的第 2 項是空白）；條件、物件的陣列每一項自己顯示的不重複
  const own = issuesAt(props.issues, props.path)
    .filter((i) => !shownInItems(kind, i, props.path, props.value))
    .map((i) => located(i, props.path))
  const inline = kind === 'boolean'
  return (
    <div className={inline ? 'flex items-center justify-between gap-3' : 'space-y-1.5'}>
      <div className={props.compact ? 'text-xs text-muted-foreground' : 'text-sm font-medium'}>
        {schema.title ?? props.name}
        {props.required && !inline ? <span className="text-destructive"> *</span> : null}
        {schema.description && !props.compact ? (
          <div className="text-xs font-normal text-muted-foreground">{schema.description}</div>
        ) : null}
      </div>
      {control(kind, props)}
      <FieldErrors issues={own} />
    </div>
  )
}
