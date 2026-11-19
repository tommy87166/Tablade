/**
 * 下方面板的「檢視」分頁：點表格裡的一格，這裡顯示完整的內容（docs/cell-view-plan.md）。
 * 只吃點的當下存下來的那一份（CellView），不呼叫 API。
 */
import { tr } from '@/i18n'
import { sourceText, type CellView } from '@/lib/cell'

/** 位置與資訊那一行：欄名（型別）、第幾列、來源檔［工作表］:行號、字數 */
function info(cell: CellView, sheetText: (name: string) => string): string[] {
  const parts = [
    cell.dtype ? tr(`${cell.column}（${cell.dtype}）`, `${cell.column} (${cell.dtype})`) : cell.column,
    tr(`第 ${cell.row.toLocaleString()} 列`, `Row ${cell.row.toLocaleString()}`),
  ]
  if (cell.source) parts.push(sourceText(cell.source.file, cell.source.sheet, cell.source.line, sheetText))
  if (cell.chars !== null)
    parts.push(
      tr(`共 ${cell.chars.toLocaleString()} 個字`, cell.chars === 1 ? '1 character' : `${cell.chars.toLocaleString()} characters`),
    )
  return parts
}

export function CellPane({ cell, sheetText }: { cell: CellView | null; sheetText: (name: string) => string }) {
  if (!cell)
    return <p className="px-3 py-2 text-sm text-muted-foreground">{tr('點表格裡的一格，這裡會顯示完整的內容', 'Click a cell in the table to see its full content here')}</p>
  const value = cell.value
  return (
    <div className="flex flex-col gap-2 px-3 py-2">
      <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
        {info(cell, sheetText).map((text, i) => (
          <span key={i} className="break-all">
            {text}
          </span>
        ))}
      </p>
      {value === null ? (
        <p className="text-sm">
          <span className="font-mono text-muted-foreground italic">null</span>
          <span className="ml-2 text-xs text-muted-foreground">{tr('空值', 'Null')}</span>
        </p>
      ) : value === '' ? (
        <p className="text-sm text-muted-foreground">{tr('（空字串）', '(empty string)')}</p>
      ) : (
        <pre className="max-w-full self-start rounded-md border bg-muted/40 px-2 py-1 font-mono text-sm whitespace-pre-wrap select-text [overflow-wrap:anywhere]">
          {String(value)}
        </pre>
      )}
    </div>
  )
}
