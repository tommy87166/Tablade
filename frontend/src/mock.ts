/**
 * 開發用的假 API：用一般瀏覽器跑 `npm run dev` 時，沒有 pywebview 也能看畫面。
 * 只在 import.meta.env.DEV 時載入，不會進正式版。資料是虛構的門市銷售資料。
 */
import type { Cell, TabladeApi } from './bridge'

const columns = ['日期', '門市', '品號', '品名', '備註', '金額']
const stores = ['北區門市', '中區門市', '南區門市']
const items: [string, string, number][] = [
  ['A001', '原子筆', 25],
  ['B020', '折疊傘', 320],
  ['C100', '無線耳機', 1290],
]

const preview: Cell[][] = Array.from({ length: 40 }, (_, i) => {
  const [code, name, price] = items[i % items.length]
  const qty = i % 7 === 0 ? -2 : (i % 5) + 1
  const amount = price * qty
  return [
    `115/01/${String((i % 28) + 1).padStart(2, '0')}`,
    stores[i % stores.length],
    code,
    name,
    i % 9 === 0 ? null : `網路|信用卡|單號 ${String(i + 1).padStart(4, '0')}`,
    amount < 0 ? `(${(-amount).toLocaleString()})` : amount.toLocaleString(),
  ]
})

const delay = <T,>(value: T, ms = 300) => new Promise<T>((r) => setTimeout(() => r(value), ms))

export const mockApi: TabladeApi = {
  ready: () => delay({ ok: true, version: '0.1.0', polars: '2.1.0', platform: 'Browser（假資料）', smoke: false }, 0),
  selftest: () =>
    delay({
      ok: true,
      result: {
        ok: true,
        checks: [
          { name: '產生假資料 CSV', ok: true, detail: '12,205 bytes（cp950）' },
          { name: '讀取 CSV（自動偵測編碼）', ok: true, detail: '200 列 × 6 欄，編碼 cp950' },
          { name: '寫出 Parquet', ok: true, detail: '4,342 bytes' },
        ],
        versions: { tablade: '0.1.0', polars: '2.1.0' },
      },
    }),
  smoke_done: () => delay({ ok: true }, 0),
  open_csv: () =>
    delay({ ok: true, cancelled: false, file: '明細_01.csv', encoding: 'cp950', bytes: 3081, rows: 200, columns, preview }),
  export_parquet: () => delay({ ok: true, cancelled: false, file: '明細_01.parquet', rows: 200, bytes: 4342 }),
}
