/**
 * 步驟的參數表單：完全依 catalog 裡的 JSON Schema 產生（DESIGN.md §16.5）。
 * 編輯先存在本地草稿，停止輸入 350ms 後送給 Python 驗證並重算；錯誤依 path 顯示在欄位下方。
 * 父元件以步驟 id 當 key：換步驟時重新掛載，尚未送出的修改會在卸載前送出。
 */
import { useEffect, useRef, useState } from 'react'

import type { CatalogItem, StepState } from '../../bridge'
import { editableFields, resolve } from '../../lib/schema'
import { StepScope } from '../StepScope'
import { Field, FieldErrors } from './fields'

const DEBOUNCE_MS = 350

interface Props {
  step: StepState
  item: CatalogItem
  onChange: (values: Record<string, unknown>) => void
}

export function StepForm({ step, item, onChange }: Props) {
  const [draft, setDraft] = useState<Record<string, unknown>>(step.params)
  const timer = useRef<number | undefined>(undefined)
  const pending = useRef(false)
  const latest = useRef({ draft, onChange })
  useEffect(() => {
    latest.current = { draft, onChange }
  })

  // undo/redo 或 Python 正規化過參數時，以 Python 的版本為準（使用者正在輸入時不覆蓋）
  const serverKey = JSON.stringify(step.params)
  useEffect(() => {
    if (!pending.current) setDraft(step.params)
  }, [serverKey, step.params])

  useEffect(
    () => () => {
      window.clearTimeout(timer.current)
      if (pending.current) latest.current.onChange(latest.current.draft)
    },
    [],
  )

  const set = (name: string, value: unknown) => {
    const next = { ...latest.current.draft }
    if (value === undefined) delete next[name]
    else next[name] = value
    setDraft(next)
    latest.current.draft = next
    pending.current = true
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      pending.current = false
      latest.current.onChange(next)
    }, DEBOUNCE_MS)
  }

  const schema = item.schema
  const fields = editableFields(schema)
  const required = new Set(schema.required ?? [])
  const shown = new Set(fields.map(([n]) => n))
  const stray = step.errors.filter((i) => !shown.has(i.path.split(/[.[]/)[0]))

  return (
    <div className="space-y-4">
      <div>
        <div className="font-medium">{item.label}</div>
        <StepScope scope={item.scope} />
        <p className="text-xs text-muted-foreground">{item.description}</p>
      </div>
      {fields.map(([name, prop]) => (
        <Field
          key={name}
          name={name}
          path={name}
          schema={prop}
          root={schema}
          value={name in draft ? draft[name] : resolve(prop, schema).default}
          onChange={(v) => set(name, v)}
          columns={step.input_columns}
          files={step.input_files}
          issues={step.errors}
          required={required.has(name)}
        />
      ))}
      {fields.length === 0 ? <p className="text-sm text-muted-foreground">這個步驟沒有參數。</p> : null}
      <FieldErrors issues={stray} />
      {step.run_error ? <p className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">執行失敗：{step.run_error}</p> : null}
    </div>
  )
}
