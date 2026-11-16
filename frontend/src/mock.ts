/**
 * 開發用的假 API：用一般瀏覽器跑 `npm run dev` 時，沒有 pywebview 也能看畫面。
 * 只在 import.meta.env.DEV 時載入，不會進正式版。
 *
 * 資料來自 `uv run python scripts/gen_frontend_dev_data.py` 產生的 src/dev/sample.json（不進版控）：
 * 用假資料跑過範例 recipe 的真實畫面狀態。選取步驟會切換畫面；其他修改只回傳目前的狀態。
 */
import type { Cell, ReadyInfo, RowsPage, TabladeApi, ViewState } from './bridge'

interface Sample {
  ready: { ok: true } & ReadyInfo
  states: Record<string, ViewState>
  rows: Record<string, { ok: true } & RowsPage>
}

const files = import.meta.glob<Sample>('./dev/sample.json', { eager: true, import: 'default' })
const sample = Object.values(files)[0]

const missing = '找不到 src/dev/sample.json：請先執行 uv run python scripts/gen_frontend_dev_data.py'
let selected = sample ? sample.states[String(Math.max(...Object.keys(sample.states).map(Number)))].selected : -1

const delay = <T,>(value: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(value), ms))
const current = () => sample.states[String(selected)]
const withState = <T extends object>(extra?: T) => delay({ ok: true as const, state: current(), ...(extra ?? ({} as T)) })
const fail = () => delay({ ok: false as const, error: missing })

export const mockApi: TabladeApi = sample
  ? {
      ready: () => delay({ ...sample.ready, platform: 'Browser（開發用假資料）' }, 0),
      selftest: () =>
        delay({ ok: true, result: { ok: true, checks: [{ name: '假的自我檢測', ok: true, detail: '開發模式' }], versions: { mode: 'dev' } } }),
      smoke_done: () => delay({ ok: true }),
      smoke_example: () => withState({ issues: [] }),
      state: () => withState(),
      rows: (step, offset, limit) => {
        const page = sample.rows[String(step)]
        return delay({ ...page, offset, rows: page.rows.slice(offset, offset + limit) as Cell[][] })
      },
      find_row: () => delay({ ok: true, row: 0 }),
      new_recipe: () => withState(),
      open_recipe: () => withState({ cancelled: true }),
      save_recipe: () => withState({ cancelled: true }),
      save_recipe_as: () => withState({ cancelled: true }),
      open_data: () => withState({ cancelled: true }),
      set_input: () => withState(),
      set_output: () => withState(),
      set_meta: () => withState(),
      add_step: () => withState({ index: selected }),
      update_step: () => withState(),
      move_step: () => withState(),
      toggle_step: () => withState(),
      delete_step: () => withState(),
      select_step: (index) => {
        if (String(index) in sample.states) selected = index
        return withState()
      },
      undo: () => withState(),
      redo: () => withState(),
      export_parquet: () => delay({ ok: true, cancelled: true }),
      error_report: () => delay({ ok: true, text: 'Tablade 驗證結果（開發用假資料）\n\n沒有發現問題。' }),
      download_skill: () => delay({ ok: true, cancelled: true }),
    }
  : (new Proxy({}, { get: (_, name) => (name === 'ready' ? fail : fail) }) as TabladeApi)
