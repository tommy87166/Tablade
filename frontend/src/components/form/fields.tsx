/** 表單欄位元件：依 JSON Schema 的型別與 ui 提示挑選（DESIGN.md §16.5）。 */
import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import type { Issue, JsonSchema } from '../../bridge'
import { defaultFor, fieldKind, issuesAt, resolve, variants, type FieldKind } from '../../lib/schema'

export interface FieldProps {
  name: string
  path: string
  schema: JsonSchema
  root: JsonSchema
  value: unknown
  onChange: (value: unknown) => void
  columns: string[] | null
  /** 這一步之前的來源檔（來源檔順序用） */
  files?: string[] | null
  issues: Issue[]
  required: boolean
}

export function FieldErrors({ issues }: { issues: Issue[] }) {
  if (!issues.length) return null
  return (
    <ul className="mt-1 space-y-0.5 text-xs text-destructive">
      {issues.map((i) => (
        <li key={`${i.path}:${i.error}`}>
          {i.error}
          {i.hint ? `（${i.hint}）` : ''}
        </li>
      ))}
    </ul>
  )
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
        <SelectValue placeholder="請選擇" />
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

function ColumnSelect({ value, onChange, columns }: { value: unknown; onChange: (v: unknown) => void; columns: string[] | null }) {
  const current = asString(value)
  if (!columns) return <Input value={current} placeholder="欄位名稱" onChange={(e) => onChange(e.target.value)} />
  const options = columns.includes(current) || !current ? columns : [current, ...columns]
  return (
    <Select value={current} onValueChange={onChange}>
      <SelectTrigger className="w-full">
        <SelectValue placeholder="選擇欄位" />
      </SelectTrigger>
      <SelectContent>
        {options.map((c) => (
          <SelectItem key={c} value={c}>
            {columns.includes(c) ? c : `${c}（不存在）`}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function ColumnChecklist({ value, onChange, columns }: { value: unknown; onChange: (v: unknown) => void; columns: string[] | null }) {
  const selected = asList(value)
  if (!columns) return <TagsEditor value={selected} onChange={onChange} placeholder="欄位名稱" />
  const toggle = (name: string, on: boolean) =>
    onChange(on ? columns.filter((c) => c === name || selected.includes(c)) : selected.filter((c) => c !== name))
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 rounded-md border p-2">
      {columns.map((c) => (
        <label key={c} className="flex items-center gap-1.5 text-sm">
          <Checkbox checked={selected.includes(c)} onCheckedChange={(on) => toggle(c, on === true)} />
          {c}
        </label>
      ))}
      {selected
        .filter((c) => !columns.includes(c))
        .map((c) => (
          <span key={c} className="text-sm text-destructive">
            {c}（不存在）
          </span>
        ))}
    </div>
  )
}

/** 依位置選欄：外觀和 ColumnChecklist 一樣，存的是第幾個資料欄（從 1 起）。 */
function PositionChecklist({ value, onChange, columns }: { value: unknown; onChange: (v: unknown) => void; columns: string[] | null }) {
  const selected = Array.isArray(value) ? value.filter((v): v is number => typeof v === 'number') : []
  if (!columns) {
    return (
      <Input
        value={selected.join(', ')}
        placeholder="例如 2, 5"
        onChange={(e) =>
          onChange(
            e.target.value
              .split(/[,，\s]+/)
              .map((t) => Number.parseInt(t, 10))
              .filter((n) => Number.isInteger(n)),
          )
        }
      />
    )
  }
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
            第 {p} 欄（不存在）
          </span>
        ))}
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
          <Button variant="ghost" size="icon-sm" aria-label="移除" onClick={() => onChange(items.filter((_, k) => k !== i))}>
            <X />
          </Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange([...items, ''])}>
        <Plus /> 新增
      </Button>
    </div>
  )
}

/** 來源檔順序：列出目前的來源檔，可上下移動；存成完整的順序。 */
function SourceFilesOrder({ value, onChange, files }: { value: unknown; onChange: (v: unknown) => void; files: string[] | null }) {
  const listed = asList(value)
  if (!files) return <TagsEditor value={listed} onChange={onChange} placeholder="來源檔名稱" />
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
            <Button variant="ghost" size="icon-xs" aria-label={`上移 ${f}`} disabled={i === 0} onClick={() => move(i, i - 1)}>
              <ArrowUp />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={`下移 ${f}`}
              disabled={i === order.length - 1}
              onClick={() => move(i, i + 1)}
            >
              <ArrowDown />
            </Button>
          </li>
        ))}
      </ol>
      {missing.length ? <p className="text-xs text-muted-foreground">目前的資料沒有：{missing.join('、')}</p> : null}
      {listed.length ? (
        <Button variant="outline" size="sm" onClick={() => onChange([])}>
          恢復原本的順序
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
      base[name] = items[i][name] ?? defaultFor(prop, root)
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
            <span className={label}>欄位</span>
            <ColumnSelect value={cond.column} columns={columns} onChange={(c) => update(i, { ...cond, column: String(c) })} />
            <Button variant="ghost" size="icon-sm" aria-label="移除條件" onClick={() => onChange(items.filter((_, k) => k !== i))}>
              <X />
            </Button>
            {at('column').length ? (
              <div className="col-span-2 col-start-2">
                <FieldErrors issues={at('column')} />
              </div>
            ) : null}

            <span className={label}>條件</span>
            <div className="col-span-2">
              <Select value={cond.op ?? ''} onValueChange={(op) => changeOp(i, op)}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="選擇條件" />
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
            <div className="col-span-3 empty:hidden">
              <FieldErrors
                issues={issuesAt(issues, `${path}[${i}]`).filter(
                  (x) => !['column', ...extra.map(([n]) => n)].some((n) => x.path.endsWith(`.${n}`)),
                )}
              />
            </div>
          </div>
        )
      })}
      <Button variant="outline" size="sm" onClick={() => onChange([...items, { column: columns?.[0] ?? '', op: 'equals', value: '' }])}>
        <Plus /> 新增條件
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
      return <TagsEditor value={value} onChange={onChange} placeholder="欄名" />
    case 'conditions':
      return <ConditionsEditor {...props} />
    case 'source_files':
      return <SourceFilesOrder value={value} onChange={onChange} files={props.files ?? null} />
    case 'enum':
      return <EnumSelect value={value} onChange={onChange} schema={schema} />
    case 'boolean':
      return <Switch checked={value === true} onCheckedChange={onChange} />
    case 'integer':
    case 'number':
      return <NumberInput value={value} onChange={onChange} integer={kind === 'integer'} />
    case 'regex':
      return <TextInput value={value} onChange={onChange} schema={schema} placeholder="例如 ^\d{3}/" />
    default:
      return <TextInput value={value} onChange={onChange} schema={schema} />
  }
}

export function Field(props: FieldProps & { compact?: boolean }) {
  const schema = resolve(props.schema, props.root)
  const kind = fieldKind(props.schema, props.root)
  const own = props.issues.filter((i) => i.path === props.path)
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
      {kind !== 'conditions' ? <FieldErrors issues={own} /> : null}
    </div>
  )
}
