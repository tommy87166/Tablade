/**
 * 「檢視」分頁要顯示的那一格（docs/cell-view-plan.md）。
 * 純函式、沒有任何 import：frontend/tests/cell.test.ts 用 node --test 直接跑。
 */

/** 儲存格的值（和 bridge.ts 的 Cell 相同；這個檔不 import 才能直接測） */
export type CellValue = string | number | boolean | null

export interface CellView {
  column: string
  dtype: string | null
  /** 整個結果的第幾列（從 1 起） */
  row: number
  value: CellValue
  /** 來源檔、工作表、行號；那一步之後沒有這些系統欄位時是 null */
  source: { file: CellValue; sheet: string | null; line: CellValue } | null
  /** 文字的字元數（不是位元組數）；不是文字欄時是 null */
  chars: number | null
}

/**
 * 點到的那一格。columns 是那一頁資料的欄名（含系統欄位），row 是那一列的值，
 * rowIndex 是整個結果的第幾列（從 0 起）。column 不在 columns 裡時回傳 null。
 */
export function cellView(columns: string[], row: CellValue[], rowIndex: number, column: string, dtype: string | null): CellView | null {
  const i = columns.indexOf(column)
  if (i < 0) return null
  const at = (name: string): CellValue | undefined => {
    const k = columns.indexOf(name)
    return k < 0 ? undefined : row[k]
  }
  const file = at('__source_file')
  const sheet = at('__sheet')
  const line = at('__line_no')
  const value = row[i] ?? null
  return {
    column,
    dtype,
    row: rowIndex + 1,
    value,
    source:
      file === undefined && line === undefined && sheet === undefined
        ? null
        : { file: file ?? null, sheet: sheet == null ? null : String(sheet), line: line ?? null },
    // 小數、日期也是用文字送過來的：型別是文字（或不知道型別）時才算字數
    chars: typeof value === 'string' && (dtype === null || dtype === 'String') ? [...value].length : null,
  }
}

/**
 * 「@階段／來源檔［工作表］:行號」的字串（警告分頁的位置、「檢視」分頁的來源）。
 * sheetText 把工作表名稱包上括號（中英文不同，由呼叫的地方傳進來）。
 */
export function sourceText(
  file: CellValue | undefined,
  sheet: string | null | undefined,
  line: CellValue | undefined,
  sheetText: (name: string) => string,
  stage?: string | null,
  slash = '／', // i18n-ignore：來源名稱的寫法（和資料裡的 __source_file 一樣，英文畫面也是）
): string {
  return `${stage ? `@${stage}${slash}` : ''}${file}${sheet ? sheetText(sheet) : ''}:${line}`
}
