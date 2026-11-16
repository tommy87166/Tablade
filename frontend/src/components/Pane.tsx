/** 三個面板共用的標題列，以及收合後留下的窄邊（點一下展開）。 */
import { PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen } from 'lucide-react'
import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

export const RAIL_WIDTH = 36

export function PaneHeader({ children }: { children: ReactNode }) {
  return <div className="flex h-9 shrink-0 items-center gap-2 border-b px-2">{children}</div>
}

export function PaneTitle({ children }: { children: ReactNode }) {
  return <span className="shrink-0 px-1 text-sm font-medium">{children}</span>
}

export function CollapseButton({ side, label, onClick }: { side: 'left' | 'right'; label: string; onClick: () => void }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon-xs" aria-label={`收合${label}`} onClick={onClick}>
          {side === 'left' ? <PanelLeftClose /> : <PanelRightClose />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>收合{label}</TooltipContent>
    </Tooltip>
  )
}

/** 標題旁的補充說明（灰色、較小）。 */
export function PaneMeta({ children }: { children: ReactNode }) {
  return <span className="min-w-0 truncate text-xs text-muted-foreground tabular-nums">{children}</span>
}

/**
 * 可收合的側邊面板內容：面板寬度只剩窄邊時改顯示 CollapsedRail。
 * 用 container query 依實際寬度切換，不靠 onResize（收合、展開、拖曳都一致）。
 */
export function CollapsiblePane({
  side,
  label,
  onExpand,
  children,
}: {
  side: 'left' | 'right'
  label: string
  onExpand: () => void
  children: ReactNode
}) {
  return (
    <div className="@container h-full">
      <div className="hidden h-full @max-[4rem]:block">
        <CollapsedRail side={side} label={label} onExpand={onExpand} />
      </div>
      <div className="h-full @max-[4rem]:hidden">{children}</div>
    </div>
  )
}

export function CollapsedRail({ side, label, onExpand }: { side: 'left' | 'right'; label: string; onExpand: () => void }) {
  return (
    <button
      type="button"
      aria-label={`展開${label}`}
      title={`展開${label}`}
      onClick={onExpand}
      className="flex h-full w-full flex-col items-center gap-2 bg-muted/30 pt-2 text-muted-foreground hover:bg-muted hover:text-foreground"
    >
      {side === 'left' ? <PanelLeftOpen className="size-4" /> : <PanelRightOpen className="size-4" />}
      <span className="text-sm font-medium [writing-mode:vertical-rl]">{label}</span>
    </button>
  )
}
