/**
 * 畫面的語言：繁體中文、英文（docs/i18n-plan.md）。
 *
 * - 不用 i18n 套件：每個字串寫成 `tr('中文', 'English')`，兩種語言放在一起，好對照也不會漏翻。
 * - 語言放在 Python（`state.lang`）：啟動時送作業系統的語言（`detectLang`），切換時再送一次；不記住選擇。
 * - 不能在模組最上層用 `tr()` 算好常數（會停在載入時的語言），要寫成函式或在元件裡算。
 *   切換語言時整個畫面會重畫（App 的狀態變了，元件都沒有 memo）。
 * - `tests/test_i18n.py` 檢查：前端的中文只能出現在 `tr()` 的第一個參數。
 */

export type Lang = 'zh' | 'en'

export const LANGS: { value: Lang; label: string }[] = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'English' },
]

/** 作業系統（WebView）的語言是中文就用中文，其他用英文。 */
export function detectLang(): Lang {
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language]
  return languages[0]?.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

let current: Lang = detectLang()

export function lang(): Lang {
  return current
}

export function setLang(value: Lang) {
  current = value
  document.documentElement.lang = value === 'zh' ? 'zh-Hant' : 'en'
}

/** 依目前的語言選一個 */
export function tr(zh: string, en: string): string {
  return current === 'en' ? en : zh
}

/** 英文的單複數：`plural(3, 'row', 'rows')` → "3 rows"（中文不用，直接寫在 tr 的第一個參數） */
export function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`
}
