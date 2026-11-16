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
  ui?: 'column' | 'columns' | 'new_columns' | 'regex'
  ui_labels?: Record<string, string>
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  minItems?: number
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
  status: 'ok' | 'error' | 'pending' | 'disabled'
  describe: string | null
  rows: number | null
  delta: number | null
  input_columns: string[] | null
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
}

export interface SourceFile {
  path: string
  bytes: number
  sha256: string
  encoding: string
  rows: number
}

export interface ViewState {
  version: { tablade: string; recipe: string }
  recipe: { path: string | null; name: string; description: string; dirty: boolean; base_dir: string | null }
  input: {
    id: string
    pattern: string
    reader: 'text_lines' | 'csv'
    encoding: string
    error: string | null
    /** 預覽用的列數（大檔時是樣本的列數） */
    rows: number | null
    /** 所有來源檔的總行數 */
    total_rows: number | null
    /** 資料太大，預覽只用每個檔案的前 head 行與最後 tail 行 */
    sampled: boolean
    sample: { head: number; tail: number }
    columns: string[] | null
    files: SourceFile[]
  }
  output: { format: string; path: string; keep_system_columns: boolean }
  steps: StepState[]
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

export type Cell = string | number | boolean | null

/** 讀檔、轉碼、匯出時由 Python 推送（pywebview 的 window.state.progress） */
export interface Progress {
  stage: string
  file: string
  done: number
  total: number
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
  smoke_example(): Promise<WithState<{ issues: Issue[] }>>
  state(): Promise<WithState>
  rows(step: number, offset: number, limit: number): Promise<Result<RowsPage>>
  find_row(step: number, source_file: string, line_no: number): Promise<Result<{ row: number | null }>>
  new_recipe(): Promise<WithState>
  open_recipe(): Promise<Dialog<{ issues: Issue[] }>>
  save_recipe(): Promise<Dialog<{ path: string }>>
  save_recipe_as(): Promise<Dialog<{ path: string }>>
  open_data(): Promise<Dialog>
  set_input(values: Partial<ViewState['input']>): Promise<WithState>
  set_output(values: Partial<ViewState['output']>): Promise<WithState>
  set_meta(values: { name?: string; description?: string }): Promise<WithState>
  add_step(
    type_name: string,
    context?: { column?: string; value?: Cell; exclude?: boolean },
  ): Promise<WithState<{ index: number }>>
  update_step(index: number, values: Record<string, unknown>): Promise<WithState>
  move_step(index: number, to: number): Promise<WithState>
  toggle_step(index: number): Promise<WithState>
  delete_step(index: number): Promise<WithState>
  select_step(index: number): Promise<WithState>
  undo(): Promise<WithState>
  redo(): Promise<WithState>
  export_parquet(): Promise<
    Result<{ cancelled: boolean; path?: string; rows?: number; columns?: number; warnings?: number; mode?: string }>
  >
  error_report(): Promise<Result<{ text: string }>>
  download_skill(): Promise<Result<{ cancelled: boolean; path?: string; bytes?: number; version?: string }>>
}

declare global {
  interface Window {
    pywebview?: { api: TabladeApi; state?: EventTarget & { progress?: Progress | null } }
  }
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
