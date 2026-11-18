/**
 * 與 Python（src/tablade/api.py、session.py）溝通的型別與取得方式。
 *
 * pywebview 會把 Api 類別的公開方法放在 window.pywebview.api，每個方法回傳 Promise。
 * 所有方法都回傳 { ok: true, ... } 或 { ok: false, error, issues? }，不會丟出例外。
 * 狀態由 Python 管理：會修改 recipe 的方法都回傳新的 state（DESIGN.md §16.4）。
 */

export type Ok<T> = { ok: true } & T
export type Fail = { ok: false; error: string; issues?: Issue[] }
export type Result<T> = Ok<T> | Fail

export interface Issue {
  path: string
  error: string
  hint?: string
  severity: 'error' | 'warning'
}

export type JsonSchema = {
  type?: string | string[]
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  const?: unknown
  ui?:
    | 'column'
    | 'columns'
    | 'column_positions'
    | 'column_order'
    | 'rename_map'
    | 'int_list'
    | 'new_columns'
    | 'regex'
    | 'source_files'
    | 'stage'
    | 'right_column'
    | 'right_columns'
    | 'data_values'
    | 'option_cards'
  /** ui 是 data_values 時：從哪個參數指定的欄讀出值 */
  ui_from?: string
  ui_labels?: Record<string, string>
  /** 選到某個選項時，顯示在選單下面的說明（例如日期樣式的正規表示式）；選項卡片時是每張卡片的說明 */
  ui_option_hints?: Record<string, string>
  /** 選項卡片的小圖（OptionIcon 的名稱，例如 venn_left） */
  ui_option_icons?: Record<string, string>
  /** 其他參數是這些值時才顯示這個欄位（例如刪除欄依位置時才顯示 positions）；值是陣列時是其中之一 */
  ui_show_if?: Record<string, unknown>
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  minItems?: number
  additionalProperties?: boolean | JsonSchema
  minProperties?: number
  minLength?: number
  maxLength?: number
  minimum?: number
  oneOf?: JsonSchema[]
  anyOf?: JsonSchema[]
  discriminator?: { propertyName: string; mapping: Record<string, string> }
  $ref?: string
  $defs?: Record<string, JsonSchema>
}

export interface CatalogItem {
  type: string
  label: string
  category: string
  description: string
  /** 「每個來源檔分別處理」或「與來源檔分檔無關」 */
  scope: string
  schema: JsonSchema
}

export interface ReadyInfo {
  version: string
  recipe_version: string
  polars: string
  platform: string
  smoke: boolean
  catalog: CatalogItem[]
}

export interface SelftestCheck {
  name: string
  ok: boolean
  detail: string
  /** 這一項確認的是什麼 */
  purpose?: string
  /** false：前面的檢查失敗，這一項沒有執行 */
  ran?: boolean
}

export interface SelftestResult {
  ok: boolean
  checks: SelftestCheck[]
  versions: Record<string, string>
}

export type StepParams = { id: string; type: string; enabled?: boolean } & Record<string, unknown>

export interface StepState {
  index: number
  id: string
  type: string
  label: string
  category: string
  enabled: boolean
  status: 'ok' | 'error' | 'pending' | 'disabled' | 'waiting'
  describe: string | null
  rows: number | null
  delta: number | null
  input_columns: string[] | null
  /** 這一步處理後有幾個來源檔 */
  files: number | null
  /** 這一步之前的來源檔（依目前順序） */
  input_files: string[] | null
  warning_count: number
  errors: Issue[]
  run_error: string | null
  params: StepParams
}

export interface RowWarning {
  step: string
  message: string
  source_file: string | null
  line_no: number | null
  /** 讀 Excel 全部工作表時，這一列在哪個工作表 */
  sheet?: string | null
  /** 從前面的階段讀進來的列：從哪個階段讀的 */
  stage?: string | null
  /** 合併來源檔之後的列：合併前的位置 */
  orig_source_file?: string | null
  orig_line_no?: number | null
  orig_sheet?: string | null
  orig_stage?: string | null
}

/** 最左邊的階段分頁 */
export interface StageState {
  index: number
  name: string
  reader: string
  /** 最後一個階段：它的結果寫到輸出檔 */
  last: boolean
  status: 'ok' | 'waiting' | 'error'
  rows: number | null
  /** 這個階段結果的資料欄（結合的右表欄位選單） */
  columns: string[] | null
  /** 結果是用樣本算的 */
  sampled: boolean
  /** 已經「完整跑一次」，後面的階段用完整的結果 */
  full: boolean
  /** 用到哪些前面的階段 */
  uses: string[]
}

export interface SourceFile {
  path: string
  bytes: number
  sha256: string
  encoding: string
  rows: number
  /** 讀 Excel 全部工作表時，這一個來源是哪個工作表 */
  sheet?: string | null
}

export interface ViewState {
  version: { tablade: string; recipe: string }
  recipe: {
    path: string | null
    name: string
    description: string
    dirty: boolean
    base_dir: string | null
    /** false：還沒有新增或開啟 Recipe（顯示起始頁） */
    open: boolean
  }
  /** 每個階段；最後一個的結果寫到輸出檔 */
  stages: StageState[]
  /** 目前在編輯的階段（下面的 input、steps、preview 都是這個階段的） */
  current: number
  input: {
    /** 檔案清單：相對於 Recipe 資料夾的路徑或絕對路徑，可含 * */
    files: string[]
    /** 讀取方式是「前面階段的輸出」時，要讀的階段 */
    stages: string[]
    /** 目前階段前面的階段（只能讀這些） */
    earlier: string[]
    /** 檔案清單每一項找到幾個檔 */
    entries: { path: string; found: number }[]
    /** 清單不是空的，而且每一項都找得到 */
    can_load: boolean
    /** 已按「載入資料」 */
    loaded: boolean
    /** 載入之後設定或檔案變了，要重新載入 */
    stale: boolean
    reader: 'text_lines' | 'csv' | 'excel' | 'parquet' | 'stages'
    encoding: string
    /** Excel 的工作表名稱；空白代表第一個工作表，* 代表全部工作表 */
    sheet: string
    /** 讀取方式是 Excel 時，第一個 Excel 檔有哪些工作表 */
    sheets: string[]
    error: string | null
    /** 預覽用的列數（大檔時是樣本的列數） */
    rows: number | null
    /** 所有來源檔的總行數 */
    total_rows: number | null
    /** 資料太大，預覽只用每個檔案的前 head 行與最後 tail 行 */
    sampled: boolean
    sample: { head: number; tail: number }
    columns: string[] | null
    /** 載入後實際讀到的每個檔 */
    sources: SourceFile[]
  }
  output: { format: string; keep_system_columns: boolean; manifest: boolean }
  steps: StepState[]
  /** -1 是「讀取資料」，0..n-1 是步驟，steps.length 是「輸出資料」（預覽最後一步的結果） */
  selected: number
  preview: {
    /** 實際顯示的步驟（選取的步驟算不出來時，是它之前最後一個算得出來的） */
    step: number
    requested: number
    rows: number | null
    columns: { name: string; dtype: string; system: boolean }[]
    warnings: RowWarning[]
  }
  can_undo: boolean
  can_redo: boolean
  issues: Issue[]
}

/** 啟動時向 GitHub 查的最新版號（只讀版號，不下載） */
export interface UpdateInfo {
  current: string
  latest: string | null
  newer: boolean
  url: string
  error: string | null
}

export type Cell = string | number | boolean | null

/** 讀檔、轉碼、匯出時由 Python 推送（pywebview 的 window.state.progress） */
export interface Progress {
  stage: string
  file: string
  done: number
  total: number
  /** done / total 的單位：檔案大小、行數或檔案數；total 是 0 時不知道總量 */
  unit?: 'bytes' | 'rows' | 'files' | 'checks'
}

/** 處理中每個階段的狀態（Python 推送到 window.state.stages；engine/run.py 的 StageTracker） */
export interface StageRun {
  name: string
  status: 'waiting' | 'running' | 'done' | 'error'
  /** 完成時結果有幾列 */
  rows: number | null
}

/** 型別分頁的一欄（session.py 的 column_summary） */
export interface ColumnInfo {
  /** 第幾個資料欄（和刪除欄的位置相同）；系統欄位是 null */
  position: number | null
  name: string
  system: boolean
  dtype: string
  /** 型別的中文名稱：文字、整數、小數（2 位）、日期… */
  label: string
  nulls: number
  /** 空白字串的數量；只有文字欄才有 */
  blanks: number | null
  /** 最小值與最大值；只有數字和日期欄才有 */
  min: Cell
  max: Cell
  examples: Cell[]
}

export interface ColumnsInfo {
  step: number
  rows: number
  sampled: boolean
  columns: ColumnInfo[]
}

export interface RowsPage {
  offset: number
  total: number
  columns: string[]
  rows: Cell[][]
}

type WithState<T = object> = Result<{ state: ViewState } & T>
type Dialog<T = object> = WithState<{ cancelled: boolean } & Partial<T>>

export interface TabladeApi {
  ready(): Promise<Result<ReadyInfo>>
  selftest(): Promise<Result<{ result: SelftestResult }>>
  smoke_done(passed: boolean): Promise<Result<object>>
  check_update(): Promise<Result<{ update: UpdateInfo | null }>>
  smoke_example(): Promise<WithState<{ issues: Issue[] }>>
  state(): Promise<WithState>
  rows(step: number, offset: number, limit: number): Promise<Result<RowsPage>>
  column_info(step: number): Promise<Result<ColumnsInfo>>
  find_row(
    step: number,
    source_file: string,
    line_no: number,
    sheet?: string | null,
    stage?: string | null,
  ): Promise<Result<{ row: number | null }>>
  new_recipe(): Promise<Dialog<{ path: string }>>
  open_recipe(): Promise<Dialog<{ issues: Issue[] }>>
  save_recipe(): Promise<Dialog<{ path: string }>>
  save_recipe_as(): Promise<Dialog<{ path: string }>>
  add_files(): Promise<Dialog>
  add_file_entry(text: string): Promise<WithState>
  remove_file(index: number): Promise<WithState>
  load_data(): Promise<WithState>
  set_input(values: Partial<ViewState['input']>): Promise<WithState>
  set_output(values: Partial<ViewState['output']>): Promise<WithState>
  set_meta(values: { name?: string; description?: string }): Promise<WithState>
  add_stage(name: string, before?: boolean): Promise<WithState<{ index: number }>>
  rename_stage(index: number, name: string): Promise<WithState>
  move_stage(index: number, to: number): Promise<WithState>
  delete_stage(index: number): Promise<WithState>
  select_stage(index: number): Promise<WithState>
  full_run(index: number): Promise<WithState>
  add_step(type_name: string, context?: { column?: string; value?: Cell; exclude?: boolean }): Promise<WithState<{ index: number }>>
  update_step(index: number, values: Record<string, unknown>): Promise<WithState>
  refresh_values(index: number, field: string, values?: Record<string, unknown>): Promise<WithState<{ values: string[] }>>
  move_step(index: number, to: number): Promise<WithState>
  toggle_step(index: number): Promise<WithState>
  delete_step(index: number): Promise<WithState>
  select_step(index: number): Promise<WithState>
  undo(): Promise<WithState>
  redo(): Promise<WithState>
  export_parquet(): Promise<
    Result<{
      cancelled: boolean
      path?: string
      rows?: number
      columns?: number
      warnings?: number
      mode?: string
      manifest?: string | null
    }>
  >
  error_report(): Promise<Result<{ text: string }>>
  download_skill(): Promise<Result<{ cancelled: boolean; path?: string; bytes?: number; version?: string }>>
}

declare global {
  interface Window {
    pywebview?: { api: TabladeApi; state?: EventTarget & { progress?: Progress | null; stages?: StageRun[] | null } }
  }
}

/** 訂閱 Python 推送的階段狀態（處理並匯出、載入資料、完整跑一次）；回傳取消訂閱的函式。 */
export function onStages(listener: (stages: StageRun[] | null) => void): () => void {
  const state = window.pywebview?.state
  if (!state) return () => {}
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<{ key: string; value: unknown }>).detail
    if (detail?.key === 'stages') listener((detail.value as StageRun[] | null) ?? null)
  }
  state.addEventListener('change', handler)
  return () => state.removeEventListener('change', handler)
}

/** 訂閱 Python 推送的進度；回傳取消訂閱的函式。 */
export function onProgress(listener: (progress: Progress | null) => void): () => void {
  const state = window.pywebview?.state
  if (!state) return () => {}
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<{ key: string; value: unknown }>).detail
    if (detail?.key === 'progress') listener((detail.value as Progress | null) ?? null)
  }
  state.addEventListener('change', handler)
  return () => state.removeEventListener('change', handler)
}

/** 等待 pywebview 注入 API。在一般瀏覽器開發時（npm run dev）改用假的 API。 */
export function waitForApi(): Promise<TabladeApi> {
  const current = window.pywebview?.api
  if (current?.ready) return Promise.resolve(current)

  return new Promise((resolve) => {
    window.addEventListener('pywebviewready', () => resolve(window.pywebview!.api), { once: true })
    if (import.meta.env.DEV) {
      window.setTimeout(async () => {
        if (!window.pywebview?.api?.ready) resolve((await import('./mock')).mockApi)
      }, 500)
    }
  })
}
