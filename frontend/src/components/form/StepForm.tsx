/**
 * 步驟的參數表單：完全依 catalog 裡的 JSON Schema 產生（DESIGN.md §16.5）。
 * 編輯先存在本地草稿，停止輸入 350ms 後送給 Python 驗證並重算；錯誤依 path 顯示在欄位下方。
 * 父元件以階段與步驟 id 當 key：換步驟時重新掛載，尚未送出的修改會在卸載前送出。
 *
 * - 父元件送其他指令（刪除、移動、切換、復原、存檔…）之前會先呼叫 flushRef，草稿排在那個指令前面送出。
 * - 送出的修改還沒收到回應時，不拿 Python 回傳的參數蓋掉輸入框（較慢的回應不會把剛打的字改回去）。
 */
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

import { tr } from '@/i18n'
import type { CatalogItem, StepState } from '../../bridge'
import { editableFields, located, resolve, visible } from '../../lib/schema'
import { StepScope } from '../StepScope'
import { Field, FieldErrors, type FormContext } from './fields'

const DEBOUNCE_MS = 350

interface Props {
  step: StepState
  item: CatalogItem
  /** 送出修改；回傳的 Promise 結束代表 Python 已經回應 */
  onChange: (values: Record<string, unknown>) => Promise<unknown> | void
  /** 前面的階段與「前往」「完整跑一次」（結合用）；params 由表單自己帶目前的草稿 */
  context?: Omit<FormContext, 'params' | 'onRefresh'>
  /** 「重新從資料讀取」：帶著表單上目前的參數（可能還沒送出） */
  onRefresh?: (field: string, values: Record<string, unknown>) => Promise<unknown> | void
  /** 表單登記「馬上送出還沒送出的修改」，父元件送其他指令之前呼叫 */
  flushRef?: RefObject<(() => void) | null>
}

export function StepForm({ step, item, onChange, context, onRefresh, flushRef }: Props) {
  const [draft, setDraft] = useState<Record<string, unknown>>(step.params)
  const timer = useRef<number | undefined>(undefined)
  /** 有修改還在等 350ms 才送出 */
  const pending = useRef(false)
  /** 已送出、Python 還沒回應的修改有幾個 */
  const inflight = useRef(0)
  /** 送出的修改全部收到回應時換一個值：再用 Python 的參數同步一次 */
  const [settled, setSettled] = useState(0)
  const latest = useRef({ draft, onChange, onRefresh })
  useEffect(() => {
    latest.current = { draft, onChange, onRefresh }
  })

  /** 送出（或「重新從資料讀取」）並記著還沒回應的數量。 */
  const track = useCallback((sent: Promise<unknown> | void) => {
    inflight.current += 1
    void Promise.resolve(sent).finally(() => {
      inflight.current -= 1
      if (inflight.current === 0) setSettled((n) => n + 1)
    })
  }, [])

  /** 還沒送出的修改馬上送出（父元件送其他指令前、表單卸載時）。 */
  const flush = useCallback(() => {
    if (!pending.current) return
    window.clearTimeout(timer.current)
    pending.current = false
    track(latest.current.onChange(latest.current.draft))
  }, [track])

  useEffect(() => {
    if (!flushRef) return
    flushRef.current = flush
    return () => {
      if (flushRef.current === flush) flushRef.current = null
    }
  }, [flushRef, flush])

  // undo/redo 或 Python 正規化過參數時，以 Python 的版本為準
  // （使用者正在輸入、或送出的修改還沒收到回應時不覆蓋：較慢的回應不會把剛打的字改回去）
  const serverKey = JSON.stringify(step.params)
  useEffect(() => {
    if (!pending.current && inflight.current === 0) setDraft(step.params)
  }, [serverKey, step.params, settled])

  useEffect(() => () => flush(), [flush])

  const schema = item.schema
  const fields = editableFields(schema)

  const set = (name: string, value: unknown) => {
    const next = { ...latest.current.draft }
    if (value === undefined) delete next[name]
    else next[name] = value
    // 因為這次修改而隱藏的欄位回到預設值（例如刪除欄改成依位置時，清掉選過的欄名）
    for (const [other, prop] of fields) if (other !== name && !visible(prop, next, schema)) delete next[other]
    setDraft(next)
    latest.current.draft = next
    pending.current = true
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      pending.current = false
      track(latest.current.onChange(next))
    }, DEBOUNCE_MS)
  }

  const required = new Set(schema.required ?? [])
  // 畫在表單上的欄位（被 ui_show_if 藏起來的不算）：其他路徑的錯誤列在最下面
  const shown = new Set(fields.filter(([, prop]) => visible(prop, draft, schema)).map(([n]) => n))
  // 前面寫是哪個參數（例如手寫的 Recipe 多了不認得的參數、藏起來的欄位有錯）
  const stray = step.errors
    .filter((i) => !shown.has(i.path.split(/[.[]/)[0]))
    .map((i) => {
      if (!i.path) return i
      const top = i.path.split(/[.[]/)[0]
      const prop = schema.properties?.[top]
      const title = (prop ? resolve(prop, schema).title : undefined) ?? top
      const inner = located(i, top)
      if (inner === i) return { ...i, error: tr(`「${title}」：${i.error}`, `“${title}”: ${i.error}`) }
      return { ...i, error: tr(`「${title}」${inner.error}`, `“${title}”, ${inner.error}`) }
    })

  return (
    <div className="space-y-4">
      <div>
        <div className="font-medium">{item.label}</div>
        <StepScope scope={item.scope} />
        <p className="text-xs text-muted-foreground">{item.description}</p>
      </div>
      {fields
        .filter(([, prop]) => visible(prop, draft, schema))
        .map(([name, prop]) => (
          <Field
            key={name}
            name={name}
            path={name}
            schema={prop}
            root={schema}
            value={name in draft ? draft[name] : resolve(prop, schema).default}
            onChange={(v) => set(name, v)}
            columns={step.input_columns}
            systemColumns={step.input_system_columns}
            files={step.input_files}
            issues={step.errors}
            required={required.has(name)}
            context={{
              stages: [],
              ...context,
              params: draft,
              onRefresh: onRefresh
                ? (field) => {
                    // 還沒送出的修改一起帶過去（Python 依這份參數讀），不再另外送
                    window.clearTimeout(timer.current)
                    pending.current = false
                    track(latest.current.onRefresh?.(field, latest.current.draft))
                  }
                : undefined,
            }}
          />
        ))}
      {fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">{tr('這個步驟沒有參數。', 'This step has no parameters.')}</p>
      ) : null}
      <FieldErrors issues={stray} />
      {step.run_error ? (
        <p className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
          {tr(`執行失敗：${step.run_error}`, `Run failed: ${step.run_error}`)}
        </p>
      ) : null}
    </div>
  )
}
