/**
 * 下方面板的「型別」分頁：目前預覽那一步每一欄的型別與概況（Python 的 session.column_info）。
 * 只有切到這個分頁時才向 Python 要資料；預覽內容變了（resetKey）就重新要。
 */
import { useEffect, useState } from 'react'

import { plural, tr } from '@/i18n'
import type { Cell, ColumnInfo, ColumnsInfo, TabladeApi } from '../bridge'

interface Props {
  api: TabladeApi
  step: number
  resetKey: string
  showSystem: boolean
}

function text(value: Cell) {
  return value === null ? '' : String(value)
}

/** 型別的顏色：文字灰、數字藍、日期綠；依 Polars 的型別判斷（顯示的名稱會跟著畫面的語言換）。 */
function tone(column: ColumnInfo) {
  const dtype = column.dtype
  if (dtype === 'String') return 'text-muted-foreground'
  if (/^(Decimal|U?Int|Float)/.test(dtype)) return 'text-sky-700 dark:text-sky-400'
  if (dtype.startsWith('Date')) return 'text-green-700 dark:text-green-400'
  return ''
}

function Count({ n, rows }: { n: number | null; rows: number }) {
  if (n === null) return <span className="text-muted-foreground/50">—</span>
  if (n === 0) return <span className="text-muted-foreground">0</span>
  const pct = rows > 0 ? Math.round((n / rows) * 100) : 0
  return (
    <span>
      {n.toLocaleString()}
      <span className="ml-1 text-xs text-muted-foreground">{tr(`（${pct < 1 ? '<1' : pct}%）`, `(${pct < 1 ? '<1' : pct}%)`)}</span>
    </span>
  )
}

export function TypesPane({ api, step, resetKey, showSystem }: Props) {
  const [info, setInfo] = useState<ColumnsInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void api.column_info(step).then((res) => {
      if (cancelled) return
      if (res.ok) {
        setInfo(res)
        setError(null)
      } else {
        setError(res.error)
      }
    })
    return () => {
      cancelled = true
    }
  }, [api, step, resetKey])

  if (error) return <p className="p-3 text-sm text-destructive">{tr(`讀不到型別：${error}`, `Couldn't read types: ${error}`)}</p>
  if (!info) return <p className="p-3 text-sm text-muted-foreground">{tr('讀取中…', 'Loading…')}</p>
  const columns = info.columns.filter((c) => showSystem || !c.system)
  if (!columns.length)
    return (
      <p className="p-3 text-sm text-muted-foreground">
        {tr('還沒有資料：載入資料後就看得到每一欄的型別。', "No data yet: load data to see each column's type.")}
      </p>
    )

  return (
    <div className="flex h-full flex-col">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-background text-xs text-muted-foreground">
          <tr className="border-b text-left">
            <th className="w-px px-3 py-1 text-right font-normal">#</th>
            <th className="w-px px-2 py-1 font-normal whitespace-nowrap">{tr('欄位', 'Column')}</th>
            <th className="w-px px-2 py-1 font-normal whitespace-nowrap">{tr('型別', 'Type')}</th>
            <th className="w-px px-2 py-1 text-right font-normal whitespace-nowrap">{tr('空值', 'Nulls')}</th>
            <th className="w-px px-2 py-1 text-right font-normal whitespace-nowrap">{tr('空白', 'Blanks')}</th>
            <th className="px-2 py-1 font-normal">{tr('範圍／範例值', 'Range / examples')}</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((c) => (
            <tr key={c.name} className={`border-b border-border/50 ${c.system ? 'text-muted-foreground' : ''}`}>
              <td className="px-3 py-1 text-right tabular-nums text-muted-foreground">{c.position ?? ''}</td>
              <td className="max-w-48 truncate px-2 py-1 font-medium whitespace-nowrap" title={c.name}>
                {c.name}
              </td>
              <td className="px-2 py-1 whitespace-nowrap">
                <span className={tone(c)}>{c.label}</span>
                <span className="ml-1.5 font-mono text-xs text-muted-foreground">{c.dtype}</span>
              </td>
              <td className="px-2 py-1 text-right whitespace-nowrap tabular-nums">
                <Count n={c.nulls} rows={info.rows} />
              </td>
              <td className="px-2 py-1 text-right whitespace-nowrap tabular-nums">
                <Count n={c.blanks} rows={info.rows} />
              </td>
              <td className="max-w-0 truncate px-2 py-1 text-muted-foreground">
                {c.min !== null || c.max !== null
                  ? tr(`${text(c.min)} ～ ${text(c.max)}`, `${text(c.min)} – ${text(c.max)}`)
                  : c.examples.map((v) => tr(`「${text(v)}」`, `"${text(v)}"`)).join(tr('、', ', '))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="px-3 py-1.5 text-xs text-muted-foreground">
        {info.sampled
          ? tr(
              `以預覽的樣本 ${info.rows.toLocaleString()} 列計算（匯出時會處理全部資料）`,
              `Computed from a preview sample of ${plural(info.rows, 'row', 'rows')} (export processes all data)`,
            )
          : tr(`共 ${info.rows.toLocaleString()} 列`, plural(info.rows, 'row', 'rows'))}
        {tr('・範例值取自前 1,000 列', ' · Examples from the first 1,000 rows')}
      </p>
    </div>
  )
}
