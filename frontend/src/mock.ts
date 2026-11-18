/**
 * 開發用的假 API：用一般瀏覽器跑 `npm run dev` 時，沒有 pywebview 也能看畫面。
 * 只在 import.meta.env.DEV 時載入，不會進正式版。
 *
 * 資料來自 `uv run python scripts/gen_frontend_dev_data.py` 產生的 src/dev/sample.json（不進版控）：
 * 用假資料跑過範例 recipe 的真實畫面狀態，鍵是「階段:步驟」。切換階段、選取步驟會切換畫面；其他修改只回傳目前的狀態。
 * 看多個階段：`uv run python scripts/gen_frontend_dev_data.py --recipe sales_combined`。
 */
import type { Cell, ColumnsInfo, Progress, ReadyInfo, RowsPage, RunState, TabladeApi, ViewState } from './bridge'

interface Sample {
  ready: { ok: true } & ReadyInfo
  states: Record<string, ViewState>
  rows: Record<string, { ok: true } & RowsPage>
  columns_info?: Record<string, { ok: true } & ColumnsInfo>
}

const files = import.meta.glob<Sample>('./dev/sample.json', { eager: true, import: 'default' })
const sample = Object.values(files)[0]

const missing = '找不到 src/dev/sample.json：請先執行 uv run python scripts/gen_frontend_dev_data.py'
const keys = sample ? Object.keys(sample.states).map((k) => k.split(':').map(Number)) : []
/** 每個階段的最後一格（「輸出資料」）是第幾格 */
const outputOf = (stage: number) => Math.max(...keys.filter(([s]) => s === stage).map(([, i]) => i))
// 跟開啟 recipe 一樣，一開始選最後一個階段的最後一步
let stageIndex = keys.length ? Math.max(...keys.map(([s]) => s)) : 0
let selected = keys.length ? outputOf(stageIndex) - 1 : -1
const key = (index = selected) => `${stageIndex}:${index}`

// 網址加 ?start 看起始頁（還沒開 Recipe），加 ?unloaded 看還沒載入資料的畫面
let open = !location.search.includes('start')
let loaded = !location.search.includes('unloaded')

const delay = <T>(value: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(value), ms))
// 每次回傳複本：React 才會察覺狀態變了
const current = () => {
  const state = structuredClone(sample.states[key()])
  state.recipe.open = open
  if (!loaded) {
    state.input = { ...state.input, loaded: false, sources: [], rows: null, total_rows: null, columns: null }
    // 跟 session.py 一樣：沒有資料時哪一步都算不出來（step 是 -1）
    state.preview = {
      ...state.preview,
      step: -1,
      rows: null,
      columns: [],
      warnings: [],
      blocked: state.preview.requested >= 0 ? { unloaded: true, step: null, stage: null } : null,
    }
    state.steps = state.steps.map((s) => ({ ...s, status: 'waiting', rows: null, files: null }))
    // 跟 session.py 一樣：還沒載入也可以匯出（匯出前先試跑）
    state.actions = { load: { ok: true, reason: null }, export: { ok: true, reason: null } }
  }
  return state
}
const withState = <T extends object>(extra?: T) => delay({ ok: true as const, state: current(), ...(extra ?? ({} as T)) })
const fail = () => delay({ ok: false as const, error: missing })

/** pywebview 的 window.state：開發時用來模擬匯出的進度（Python 那邊是 api.py 的 _push_progress） */
const progressState = new EventTarget()
const pushProgress = (value: Progress | null) =>
  progressState.dispatchEvent(new CustomEvent('change', { detail: { key: 'progress', value } }))
const pushRun = (value: RunState | null) => progressState.dispatchEvent(new CustomEvent('change', { detail: { key: 'run', value } }))

/** 假的進度視窗：先跑 phases（例如匯出的「準備檔案」「試跑」），再讓每個階段依序轉圈、打勾（列數用預覽的列數）。 */
async function fakeStages(perStage: (index: number, run: RunState) => Promise<void>, phases: string[] = []) {
  const stages = current().stages
  const run: RunState = {
    stages: stages.map((s) => ({ name: s.name, status: 'waiting', rows: null, detail: '' })),
    phases: phases.map((name) => ({ name, status: 'waiting', detail: '' })),
  }
  const push = () => pushRun(structuredClone(run))
  push()
  const files = 5
  for (const phase of run.phases) {
    phase.status = 'running'
    for (let i = 0; i < (phase.name === '準備檔案' ? files : stages.length); i++) {
      phase.detail =
        phase.name === '準備檔案'
          ? `已處理 ${i} / ${files} 個檔，正在讀 data/明細_0${i + 1}.csv`
          : `第 ${i + 1} / ${stages.length} 個階段：${stages[i].name}`
      push()
      await delay(null, 300)
    }
    phase.status = 'done'
    phase.detail = phase.name === '準備檔案' ? `已處理 ${files} / ${files} 個檔` : '沒有錯誤'
    push()
  }
  for (let i = 0; i < run.stages.length; i++) {
    run.stages[i].status = 'running'
    push()
    await perStage(i, run)
    run.stages[i] = { ...run.stages[i], status: 'done', rows: stages[i].rows, detail: '' }
    push()
  }
}

/** 假的匯出：看得到「執行（串流）」的行數與百分比，再來是沒有百分比的「寫出 Parquet」。 */
async function fakeExport() {
  const total = 4_074_302
  await fakeStages(
    async (stage) => {
      for (let i = 0; i <= 10; i++) {
        pushProgress({ stage: `階段「${stage + 1}」：執行（串流）`, file: '', done: Math.round((total * i) / 10), total, unit: 'rows' })
        await delay(null, 150)
      }
    },
    ['準備檔案', '試跑'],
  )
  pushProgress({ stage: '寫出 Parquet', file: '', done: 0, total: 0 })
  await delay(null, 1500)
  pushProgress(null)
  // 跟 Python 一樣：每個要寫檔的階段寫到 output/（開發用假資料不會真的寫檔）
  const outputs = current()
    .stages.filter((s) => s.output && !s.output.error)
    .map((s) => ({ stage: s.name, path: `（開發用假資料）/${s.output!.path}`, rows: s.rows ?? 0, manifest: null }))
  const last = outputs[outputs.length - 1]
  return {
    ok: true as const,
    cancelled: false,
    path: last?.path,
    rows: last?.rows,
    columns: 8,
    warnings: 0,
    mode: 'stream',
    manifest: null,
    outputs,
  }
}

export const mockApi: TabladeApi = sample
  ? {
      ready: () => delay({ ...sample.ready, platform: 'Browser（開發用假資料）' }, 0),
      staging_info: () => delay({ ok: true as const, path: '/tmp/tablade-stage-xxxx（開發用假資料）', bytes: 12_345_678 }),
      open_staging: () => delay({ ok: true as const, path: '/tmp/tablade-stage-xxxx（開發用假資料）', bytes: 12_345_678 }),
      selftest: () =>
        delay({
          ok: true,
          result: {
            ok: true,
            checks: [
              ['產生假資料 CSV', '可以在暫存資料夾寫檔', '12,205 bytes（cp950）'],
              ['讀取 CSV（自動偵測編碼）', '讀檔與 Big5（cp950）編碼偵測正常', '200 列 × 6 欄，編碼 cp950'],
              ['寫出 Parquet', '可以輸出 Parquet 檔', '4,342 bytes'],
              ['讀回 Parquet 並比對', '寫出的內容完全正確', '內容一致'],
              ['Lazy 查詢', 'Polars 的查詢引擎正常', 'group_by 得到 3 組'],
              ['讀 Excel', '可以讀 Excel 工作表（列號、日期、數字）', '讀回 24 列，列號、日期、數字正確（fastexcel 0.21.0）'],
              [
                '多核心轉碼',
                '大檔可以用多個程序同時轉碼，結果和單核心相同',
                '開 2 個工作程序同時轉碼，結果與單核心相同；大檔平常用 8 個工作程序',
              ],
              [
                '完整執行範例 Recipe',
                '轉碼、各檔分開處理、8 個步驟、合併來源檔、串流匯出整條流程正常',
                '2 個 cp950 檔 → 合併成 70 列，與預期一致；處理紀錄正確',
              ],
            ].map(([name, purpose, detail]) => ({ name, purpose, detail, ok: true, ran: true })),
            versions: { tablade: '0.1.0', python: '—', polars: '—', platform: 'Browser（開發用假資料）', frozen: 'False' },
          },
        }),
      smoke_done: () => delay({ ok: true }),
      // 網址加 ?update 看有新版時的提示
      check_update: () =>
        delay(
          {
            ok: true,
            update: location.search.includes('update')
              ? {
                  current: '0.1.0',
                  latest: '0.2.0',
                  newer: true,
                  url: 'https://github.com/（開發用假資料）/releases/tag/v0.2.0',
                  error: null,
                }
              : { current: '0.1.0', latest: '0.1.0', newer: false, url: 'https://github.com/（開發用假資料）/releases', error: null },
          },
          600,
        ),
      smoke_example: () => withState({ issues: [] }),
      state: () => withState(),
      rows: (step, offset, limit) => {
        const page = sample.rows[key(step)]
        return delay({ ...page, offset, rows: page.rows.slice(offset, offset + limit) as Cell[][] })
      },
      find_row: () => delay({ ok: true, row: 0 }),
      new_recipe: () => {
        open = true
        loaded = false
        return withState({ cancelled: false, path: '（開發用假資料）/未命名.recipe.json' })
      },
      open_recipe: () => {
        open = true
        loaded = false
        return withState({ cancelled: false, issues: [] })
      },
      save_recipe: () => withState({ cancelled: true }),
      save_recipe_as: () => withState({ cancelled: true }),
      add_files: () => withState({ cancelled: true }),
      add_file_entry: () => withState(),
      remove_file: () => withState(),
      load_data: async () => {
        loaded = true
        await fakeStages(() => delay(undefined, 300))
        return withState()
      },
      set_input: () => withState(),
      set_output: () => withState(),
      set_stage_output: () => withState(),
      set_meta: (values) => {
        for (const s of Object.values(sample.states)) Object.assign(s.recipe, values, { dirty: true })
        return withState()
      },
      add_stage: () => withState({ index: stageIndex }),
      rename_stage: () => withState(),
      move_stage: () => withState(),
      delete_stage: () => withState(),
      select_stage: (index) => {
        if (keys.some(([s]) => s === index)) {
          stageIndex = index
          selected = outputOf(index) - 1
        }
        return withState()
      },
      full_run: () => withState(),
      add_step: () => withState({ index: selected }),
      update_step: () => withState(),
      refresh_values: () => withState({ values: [] }),
      move_step: () => withState(),
      toggle_step: () => withState(),
      delete_step: () => withState(),
      select_step: (index) => {
        if (key(index) in sample.states) selected = index
        return withState()
      },
      undo: () => withState(),
      redo: () => withState(),
      column_info: (step) =>
        delay(sample.columns_info?.[loaded ? key(step) : ''] ?? { ok: true as const, step, rows: 0, sampled: false, columns: [] }),
      export_parquet: fakeExport,
      error_report: () => delay({ ok: true, text: 'Tablade 驗證結果（開發用假資料）\n\n沒有發現問題。' }),
      download_skill: () => delay({ ok: true, cancelled: true }),
    }
  : (new Proxy({}, { get: (_, name) => (name === 'ready' ? fail : fail) }) as TabladeApi)

// App 從 window.pywebview.state 收進度（bridge.ts 的 onProgress）
if (sample) window.pywebview = { api: mockApi, state: progressState }
