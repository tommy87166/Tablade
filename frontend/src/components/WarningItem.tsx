/**
 * 下方面板「警告」分頁的一則警告：平常一行（位置與訊息太長就截斷），點一下向下展開成完整的位置與訊息，
 * 再點一下收回。指到某一列的警告，展開時 App 會讓表格捲到那一列並整列標起來（PreviewGrid 的 scrollToRow）。
 */
import { TriangleAlert } from 'lucide-react'

/** 展開的警告與表格裡被標記的那一列用同一個顏色（PreviewGrid 也用這個） */
export const MARKED = 'bg-amber-200/80 shadow-[inset_3px_0_0_var(--color-amber-500)] dark:bg-amber-500/30'

export function WarningItem({
  place,
  message,
  open,
  onToggle,
}: {
  /** 位置（來源檔:行號…） */
  place: string
  message: string
  open: boolean
  onToggle: () => void
}) {
  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        className={`flex w-full gap-2 px-3 text-left text-xs ${open ? `items-start py-1 ${MARKED}` : 'items-center py-0.5 hover:bg-muted'}`}
        onClick={onToggle}
      >
        <TriangleAlert className={`size-3.5 shrink-0 text-amber-600 ${open ? 'mt-px' : ''}`} />
        {/* 位置最多佔一半，訊息至少有一半的寬度；展開時兩個都換行顯示完整的內容 */}
        <span className={`max-w-[50%] min-w-0 shrink-0 font-mono text-muted-foreground ${open ? 'break-all whitespace-pre-wrap' : 'truncate'}`}>
          {place}
        </span>
        <span className={`min-w-0 flex-1 ${open ? 'break-words whitespace-pre-wrap' : 'truncate'}`}>{message}</span>
      </button>
    </li>
  )
}
