/**
 * 下方面板的「檢視」分頁：點表格裡的一格，這裡顯示完整的內容（docs/cell-view-plan.md）。
 * 只吃點的當下存下來的那一份（CellView），不呼叫 API。
 *
 * 上面是完整的值（太長就捲動）；下面貼齊分頁底部的是一張小表（列高和「型別」分頁相同）：
 * 第一列是欄列（欄名:第幾列）與來自（來源檔［工作表］:行號），第二列是型別與字數（只有文字欄）。
 */
import { tr } from '@/i18n'
import { sourceText, type CellView } from '@/lib/cell'

/** 小表的每一列：兩組「名稱、內容」（第二組可能沒有） */
type Pair = [string, string]
function rows(cell: CellView, sheetText: (name: string) => string): [Pair, Pair | null][] {
  // 「來自」和「欄列」的列號不一樣：列號是這一步的結果，來源檔:行號是原本的檔
  const from: Pair | null = cell.source
    ? [tr('來自', 'From'), sourceText(cell.source.file, cell.source.sheet, cell.source.line, sheetText)]
    : null
  const chars: Pair | null = cell.chars !== null ? [tr('字數', 'Characters'), cell.chars.toLocaleString()] : null
  return [
    [[tr('欄列', 'Cell'), `${cell.column}:${cell.row.toLocaleString()}`], from],
    [[tr('型別', 'Type'), cell.dtype ?? '—'], chars],
  ]
}

const NAME = 'w-px px-3 py-0.5 text-left font-normal whitespace-nowrap text-muted-foreground'
/** 值的欄寬照內容決定（放得下就不換行，太窄時才在任何地方斷行） */
const VALUE = 'px-2 py-0.5 break-all tabular-nums'

export function CellPane({ cell, sheetText }: { cell: CellView | null; sheetText: (name: string) => string }) {
  if (!cell)
    return <p className="px-3 py-2 text-sm text-muted-foreground">{tr('點表格裡的一格，這裡會顯示完整的內容', 'Click a cell in the table to see its full content here')}</p>
  const value = cell.value
  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        {value === null ? (
          <p className="text-sm">
            <span className="font-mono text-muted-foreground italic">null</span>
            <span className="ml-2 text-xs text-muted-foreground">{tr('空值', 'Null')}</span>
          </p>
        ) : value === '' ? (
          <p className="text-sm text-muted-foreground">{tr('（空字串）', '(empty string)')}</p>
        ) : (
          <pre className="w-fit max-w-full rounded-md border bg-muted/40 px-2 py-1 font-mono text-sm whitespace-pre-wrap select-text [overflow-wrap:anywhere]">
            {String(value)}
          </pre>
        )}
      </div>
      <table className="w-full shrink-0 border-t text-xs">
        <tbody>
          {rows(cell, sheetText).map(([first, second]) => (
            <tr key={first[0]} className="border-t border-border/50 first:border-t-0">
              <th scope="row" className={NAME}>
                {first[0]}
              </th>
              <td className={VALUE}>{first[1]}</td>
              <th scope="row" className={NAME}>
                {second?.[0]}
              </th>
              <td className={VALUE}>{second?.[1]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
