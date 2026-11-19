/**
 * 與 Python（src/tablade/api.py、session.py）溝通的型別與取得方式。
 *
 * pywebview 會把 Api 類別的公開方法放在 window.pywebview.api，每個方法回傳 Promise。
 * 所有方法都回傳 { ok: true, ... } 或 { ok: false, error, issues? }，不會丟出例外。
 * 狀態由 Python 管理：會修改 recipe 的方法都回傳新的 state（DESIGN.md §16.4）。
 */

import type { Lang } from './i18n'

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
    | 'own_name'
  /** ui 是 data_values 時：從哪個參數指定的欄讀出值；own_name 時：選項是哪個參數（這一步自己的欄名清單） */
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
  /** Recipe 裡的值：手寫或 AI 寫的 Recipe 可能沒有（null） */
  id: string | null
  type: string | null
  /** 步驟名稱；不支援的類型是 type 本身（也可能是 null） */
  label: string | null
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
  /** 算不出來（pending）時：前面第一個出錯的步驟（index） */
  blocked_by: number | null
  /** 算不出來（pending）時：還沒有結果的前面階段（結合的另一張表） */
  blocked_stage: string | null
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
  /** 最後一個階段：它的結果一定寫檔 */
  last: boolean
  status: 'ok' | 'waiting' | 'error'
  /** 圓點的說明（滑鼠移上去）：「已算出結果：90 列」「第 11 步有錯」「讀的「文字明細」有錯，還算不出來」 */
  status_text: string
  rows: number | null
  /** 這個階段結果的資料欄（結合的右表欄位選單） */
  columns: string[] | null
  /** 結果是用樣本算的 */
  sampled: boolean
  /** 已經「完整跑一次」，後面的階段用完整的結果 */
  full: boolean
  /** 用到哪些前面的階段 */
  uses: string[]
  /** Recipe 裡的輸出檔名（空白：中間的階段不寫檔、最後一個階段用 Recipe 名稱） */
  output_file: string
  /** 輸出格式（必填；讀進來的 Recipe 沒有時是空字串，驗證會報錯） */
  output_format: OutputFormat | ''
  /** 要寫的檔（中間的階段沒填檔名時是 null） */
  output: StageOutput | null
}

/** 輸出檔的格式：Parquet（保留型別）或 CSV（UTF-8 加 BOM，Excel 可以直接打開） */
export type OutputFormat = 'parquet' | 'csv'

/** 一個階段要寫的檔（session.py 的 _planned_state） */
export interface StageOutput {
  /** 檔名（已照格式補上副檔名） */
  file: string
  format: OutputFormat
  /** 寫出的位置，例如 output/明細.parquet（相對於 Recipe 所在的資料夾） */
  path: string
  /** 最後一個階段沒填檔名，用 Recipe 名稱 */
  default: boolean
  /** 已經存在：匯出時會覆蓋 */
  exists: boolean
  error: string | null
}

export interface SourceFile {
  path: string
  bytes: number
  sha256: string
  encoding: string
  rows: number
  /** 讀 Excel 全部工作表時，這一個來源是哪個工作表 */
  sheet?: string | null
  /** 這個檔是哪個階段讀的（處理紀錄用） */
  stage?: string | null
  /** zip 裡的檔：zip 檔的路徑（bytes、sha256 是解開後的） */
  archive?: string | null
  archive_bytes?: number | null
  archive_sha256?: string | null
  /** 寬鬆解碼：解不開、換成 � 的字數（一個 2 位元組的字算 1 個） */
  replaced?: number
  /** 前 10 個有換字的行（同一行只記一次） */
  replaced_lines?: number[]
  /** auto＋寬鬆：UTF-8、cp950 都解不開，取樣後選了 encoding */
  auto_picked?: boolean
  /** 逐行讀取：[CRLF, 單獨的 LF, 單獨的 CR] 各有幾個 */
  endings?: [number, number, number]
  /** 只認 CRLF：照原樣留在行裡的換行有幾個 */
  inner_breaks?: number
  /** 前 10 個留著換行的行（同一行只記一次） */
  inner_break_lines?: number[]
}

/** zip 裡一個符合的檔 */
export interface ZipMember {
  path: string
  /** 解開後多大 */
  bytes: number
  /** 壓縮方式（Deflate、Deflate64…） */
  method: string
  /** 讀不了的原因（有密碼、不支援的壓縮方式…）；可以讀時是 null */
  problem: string | null
}

/** 讀取方式「zip 裡的文字檔」的掃描結果（Python 的 session._zip_state） */
export interface ZipScan {
  /** 找不到 zip、members 沒填這類讀不了的情況 */
  error: string | null
  /** 每個 zip 裡符合的檔；members 是空的代表這個 zip 沒有符合的檔（略過） */
  zips: { path: string; members: ZipMember[] }[]
  files: number
  /** 符合的檔解開後共多大 */
  bytes: number
  /** 暫存大約要多少空間 */
  estimate: number
  /** 暫存資料夾所在磁碟剩多少（讀不到時是 null） */
  free: number | null
  /** 不能載入或匯出的原因 */
  problems: string[]
}

/** 用樣本算的步驟：目前階段（stage 是空字串）的第幾步，或往上讀到、結合到的階段的第幾步 */
export interface SampledStep {
  stage: string
  number: number
  label: string
}

/** 結合的另一張表是樣本的步驟（ref 是另一張表的階段名稱） */
export interface SampledRef extends SampledStep {
  ref: string
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
  /** 工具列的「載入資料及預覽」「處理並匯出資料」能不能按 */
  actions: { load: ActionState; export: ActionState }
  /** 每個階段；最後一個的結果一定寫檔，中間的有填輸出檔名才寫 */
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
    /** 已按「載入資料及預覽」 */
    loaded: boolean
    /** 載入之後設定或檔案變了，要重新載入 */
    stale: boolean
    reader: 'text_lines' | 'zip_lines' | 'csv' | 'excel' | 'parquet' | 'stages'
    encoding: string
    /** 解不開的字：error 報錯（預設）；replace 換成 �（只有逐行讀取、zip 裡的文字檔能用） */
    decode_errors?: 'error' | 'replace'
    /** 換行：any 都算換行（預設）；crlf 只認 CRLF，欄位裡的換行留在同一行（只有逐行讀取、zip 裡的文字檔能用） */
    line_breaks?: 'any' | 'crlf'
    /** Excel 的工作表名稱；空白代表第一個工作表，* 代表全部工作表 */
    sheet: string
    /** 讀取方式是 Excel 時，第一個 Excel 檔有哪些工作表 */
    sheets: string[]
    error: string | null
    /** 預覽用的列數（大檔時是樣本的列數） */
    rows: number | null
    /** 所有來源檔的總行數 */
    total_rows: number | null
    /** 資料太大，預覽只用每個檔案的開頭與結尾（每個檔都整份取到時不算） */
    sampled: boolean
    /** 這個階段載入的樣本：共幾行、幾個檔、每檔最多前 head 行與最後 tail 行（不是樣本時是 null） */
    sample: { rows: number; files: number; head: number; tail: number } | null
    columns: string[] | null
    /** 載入後實際讀到的每個檔 */
    sources: SourceFile[]
    /** 讀取方式是「zip 裡的文字檔」時：zip 裡要讀的檔（相對路徑，可用 * ? [] **，不分大小寫） */
    members?: string
    /** 讀取方式是「zip 裡的文字檔」時的掃描結果（只讀 zip 的目錄）；其他讀取方式是 null */
    zip_scan?: ZipScan | null
    /** 來源檔的警告（寬鬆解碼換了字、只認 CRLF 時留在行裡的換行），一則一個（文字由 Python 產生） */
    file_warnings?: { file: string; text: string }[]
    /** 都算換行時：幾乎都是 CRLF、只有幾個單獨的 LF 或 CR 的檔，提示可以改成只認 CRLF */
    line_break_hints?: { file: string; text: string }[]
  }
  output: { keep_system_columns: boolean; manifest: boolean }
  steps: StepState[]
  /** -1 是「讀取資料」，0..n-1 是步驟，steps.length 是「輸出資料」（預覽最後一步的結果） */
  selected: number
  preview: {
    /** 預覽內容的指紋（內容、階段或語言變了才換；表格與型別分頁只比它，rows()／column_info() 回傳同一個）；沒有資料時是 null */
    key: string | null
    /** 實際顯示的步驟（選取的步驟算不出來時，是它之前最後一個算得出來的） */
    step: number
    requested: number
    rows: number | null
    columns: { name: string; dtype: string; system: boolean }[]
    /** 前幾則警告（每項檢查最多 100 則，這裡最多 200 則）；實際的數量是 warning_count */
    warnings: RowWarning[]
    warning_count: number
    /** 選的那一步算不出來的原因（算得出來時是 null）：還沒載入、哪一步有錯、哪個前面的階段還沒有結果 */
    blocked: { unloaded: boolean; step: number | null; stage: string | null } | null
    /** 抽樣模式（用樣本算的：列數、加總可能不正確）、完整模式（資料全部讀進來，或讀匯出檔）；沒有資料時是 null */
    mode: 'sample' | 'full' | null
    /** 這個階段和它往上讀到的階段，讀進來的檔共有幾行（沒有資料時是 null） */
    input_rows: number | null
    /** 完整模式讀的是匯出檔時：匯出檔、列數、處理了幾行輸入、匯出時間、警告總數 */
    full: { path: string; rows: number; input_rows: number; time: string; warning_count: number } | null
    /** 抽樣模式下，用樣本算、數字會不準的步驟（要看整個來源檔的：移除重複、群組彙總、樞紐）；沒有時是空的 */
    sampled_steps: SampledStep[]
    /** 抽樣模式下，結合的另一張表是樣本的步驟（對不到的鍵在完整的另一張表裡可能有）；沒有時是空的 */
    sampled_refs: SampledRef[]
  }
  can_undo: boolean
  can_redo: boolean
  /** 畫面的語言（Python 記著；啟動時前端送作業系統的語言，切換時再送一次） */
  lang: Lang
}

/** 啟動時向 GitHub 查的最新版號（只讀版號，不下載） */
export interface UpdateInfo {
  current: string
  latest: string | null
  newer: boolean
  url: string
  /** 沒查到最新版的原因（Python 啟動當下的語言；有 error_code 時前端改用自己的文字，切換語言才會跟著換） */
  error: string | null
  /** 沒查到的原因代碼：關閉檢查、還沒發布、HTTP 錯誤（http_status）、連不上、讀不出版號 */
  error_code?: 'disabled' | 'no_release' | 'http' | 'offline' | 'bad_version' | null
  http_status?: number | null
}

export type Cell = string | number | boolean | null

/** 讀檔、轉碼、匯出時由 Python 推送（pywebview 的 window.state.progress） */
export interface Progress {
  stage: string
  file: string
  done: number
  total: number
  /** done / total 的單位：檔案大小、行數或檢查的項目數；total 是 0 時不知道總量 */
  unit?: 'bytes' | 'rows' | 'checks'
}

/** 處理中每個階段的狀態（Python 推送到 window.state.run 的 stages；engine/run.py 的 StageTracker） */
export interface StageRun {
  name: string
  status: 'waiting' | 'running' | 'done' | 'error'
  /** 完成時結果有幾列 */
  rows: number | null
  /** 處理中的細節，例如「已處理 1 / 3 個檔，正在讀 明細_02.csv」 */
  detail: string
}

/** 進度視窗上階段清單上面的那幾列（匯出時的「準備檔案」「試跑」） */
export interface RunPhase {
  /** 代碼（prepare、trial）；name 是畫面上寫的字，跟著畫面的語言 */
  key: string
  name: string
  status: StageRun['status']
  detail: string
}

/** 進度視窗的狀態（Python 推送到 window.state.run；engine/run.py 的 StageTracker） */
export interface RunState {
  stages: StageRun[]
  phases: RunPhase[]
}

/** 工具列按鈕能不能按；不能按時 reason 是原因（session.py 算） */
export interface ActionState {
  ok: boolean
  reason: string | null
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
  /** 這份資料的指紋（和 state.preview.key 不同時是過時的回應） */
  key: string | null
}

export interface RowsPage {
  offset: number
  total: number
  columns: string[]
  rows: Cell[][]
  /** 這份資料的指紋（和 state.preview.key 不同時是過時的回應） */
  key: string | null
}

type WithState<T = object> = Result<{ state: ViewState } & T>
type Dialog<T = object> = WithState<{ cancelled: boolean } & Partial<T>>

export interface TabladeApi {
  ready(): Promise<Result<ReadyInfo>>
  selftest(): Promise<Result<{ result: SelftestResult }>>
  /** 暫存資料夾（轉碼後的檔、處理中的暫存檔）的位置與目前大小 */
  staging_info(): Promise<Result<{ path: string; bytes: number }>>
  /** 用檔案總管／Finder 打開暫存資料夾 */
  open_staging(): Promise<Result<{ path: string; bytes: number }>>
  smoke_done(passed: boolean): Promise<Result<object>>
  check_update(): Promise<Result<{ update: UpdateInfo | null }>>
  smoke_example(): Promise<WithState<{ issues: Issue[] }>>
  state(): Promise<WithState>
  /** 切換畫面的語言（不記住，下次開啟又跟作業系統的語言） */
  set_language(lang: Lang): Promise<WithState<{ catalog: CatalogItem[] }>>
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
  /** 改階段的輸出檔名與格式（沒給格式時不變） */
  set_stage_output(index: number, file: string, format?: OutputFormat): Promise<WithState>
  set_meta(values: { name?: string; description?: string }): Promise<WithState>
  add_stage(name: string, before?: boolean): Promise<WithState<{ index: number }>>
  rename_stage(index: number, name: string): Promise<WithState>
  move_stage(index: number, to: number): Promise<WithState>
  delete_stage(index: number): Promise<WithState>
  select_stage(index: number): Promise<WithState>
  full_run(index: number): Promise<WithState>
  add_step(type_name: string, context?: { column?: string; value?: Cell; exclude?: boolean }): Promise<WithState<{ index: number }>>
  /** values 帶的 id 和第 index 步現在的 id 不同時 Python 拒絕（表單延後送出的修改不會寫到別的步驟） */
  update_step(index: number, values: Record<string, unknown>): Promise<WithState>
  refresh_values(index: number, field: string, values?: Record<string, unknown>): Promise<WithState<{ values: string[] }>>
  move_step(index: number, to: number): Promise<WithState>
  toggle_step(index: number): Promise<WithState>
  delete_step(index: number): Promise<WithState>
  select_step(index: number): Promise<WithState>
  undo(): Promise<WithState>
  redo(): Promise<WithState>
  /** 一起回傳新的 state（「輸出資料」改讀匯出檔）；試跑出錯時也是 */
  export_parquet(): Promise<
    WithState<{
      cancelled: boolean
      path?: string
      rows?: number
      columns?: number
      warnings?: number
      /** 寬鬆解碼換了字的檔數（不算在 warnings 裡） */
      input_warnings?: number
      mode?: string
      manifest?: string | null
      /** 寫出的每個檔（依階段順序） */
      outputs?: { stage: string; path: string; rows: number; manifest: string | null; format: OutputFormat }[]
      /** 匯出前試跑出錯：哪個階段的第幾步（沒有寫出任何檔） */
      trial_error?: { message: string; stage: number | null; step: number | null }
    }>
  >
  /** 報告文字與問題清單（同一次檢查：目前的 Recipe、畫面的語言） */
  error_report(): Promise<Result<{ text: string; issues: Issue[] }>>
  download_skill(): Promise<Result<{ cancelled: boolean; path?: string; bytes?: number; version?: string }>>
}

declare global {
  interface Window {
    pywebview?: { api: TabladeApi; state?: EventTarget & { progress?: Progress | null; run?: RunState | null; usage?: Usage | null } }
  }
}

/** 訂閱 Python 推送的進度視窗狀態（處理並匯出、載入資料及預覽、完整跑一次）；回傳取消訂閱的函式。 */
export function onRun(listener: (run: RunState | null) => void): () => void {
  const state = window.pywebview?.state
  if (!state) return () => {}
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<{ key: string; value: unknown }>).detail
    if (detail?.key === 'run') listener((detail.value as RunState | null) ?? null)
  }
  state.addEventListener('change', handler)
  return () => state.removeEventListener('change', handler)
}

/** Python 每 2 秒推送的用量（bytes）：主程序（Python＋Polars）與平行轉碼的工作程序各用了多少記憶體
 *  （目前、這次開啟後最高），暫存資料夾多大。記憶體讀不到（不支援的作業系統）時是 null；不含網頁畫面。 */
export interface Usage {
  memory: number | null
  peak: number | null
  workers: number | null
  workers_peak: number | null
  staging: number
}

/** 訂閱 Python 推送的用量；回傳取消訂閱的函式。 */
export function onUsage(listener: (usage: Usage | null) => void): () => void {
  const state = window.pywebview?.state
  if (!state) return () => {}
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<{ key: string; value: unknown }>).detail
    if (detail?.key === 'usage') listener((detail.value as Usage | null) ?? null)
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
