/**
 * 與 Python（src/tablade/api.py）溝通的型別與取得方式。
 *
 * pywebview 會把 Api 類別的公開方法放在 window.pywebview.api，每個方法回傳 Promise。
 * 所有方法都回傳 { ok: true, ... } 或 { ok: false, error }，不會丟出例外。
 */

export type Result<T> = ({ ok: true } & T) | { ok: false; error: string }

export interface ReadyInfo {
  version: string
  polars: string
  platform: string
  smoke: boolean
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

export type Cell = string | number | boolean | null

export type Cancelled = { cancelled: true }

export interface CsvPreview {
  cancelled: false
  file: string
  encoding: string
  bytes: number
  rows: number
  columns: string[]
  preview: Cell[][]
}

export interface ExportInfo {
  cancelled: false
  file: string
  rows: number
  bytes: number
}

export interface TabladeApi {
  ready(): Promise<Result<ReadyInfo>>
  selftest(): Promise<Result<{ result: SelftestResult }>>
  smoke_done(passed: boolean): Promise<Result<object>>
  open_csv(): Promise<Result<CsvPreview | Cancelled>>
  export_parquet(): Promise<Result<ExportInfo | Cancelled>>
}

declare global {
  interface Window {
    pywebview?: { api: TabladeApi }
  }
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
