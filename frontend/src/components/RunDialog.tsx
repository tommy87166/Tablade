/**
 * 處理中的階段進度視窗（處理並匯出資料、載入資料、完整跑一次）：每個階段一列，
 * 等待中是灰點、處理中轉圈（旁邊是處理到第幾行）、完成打勾（結果有幾列）、失敗紅叉。
 * 匯出跑完後留著當結果摘要，按「關閉」才收起來；狀態由 Python 推送（bridge.ts 的 onStages）。
 */
import { Check, Loader2, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { StageRun } from '../bridge'

export function StageRunIcon({ status, className = 'size-3.5' }: { status: StageRun['status']; className?: string }) {
  if (status === 'running') return <Loader2 className={`${className} shrink-0 animate-spin text-primary`} aria-label="處理中" />
  if (status === 'done') return <Check className={`${className} shrink-0 text-green-600`} aria-label="完成" />
  if (status === 'error') return <X className={`${className} shrink-0 text-destructive`} aria-label="失敗" />
  return (
    <span className="flex size-3.5 shrink-0 items-center justify-center" aria-label="等待中">
      <span className="size-1.5 rounded-full bg-muted-foreground/40" />
    </span>
  )
}

interface Props {
  title: string
  stages: StageRun[]
  /** 處理中那個階段的細節（例如「12,345 / 80,000 行（15%）」） */
  detail: string
  busy: boolean
  /** 跑完後的摘要（例如匯出到哪裡、幾列） */
  result?: string
  failed?: boolean
  onClose: () => void
}

export function RunDialog({ title, stages, detail, busy, result, failed, onClose }: Props) {
  const done = stages.filter((s) => s.status === 'done').length
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-md" showCloseButton={!busy}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {busy ? `階段依序執行：${done} / ${stages.length} 完成` : failed ? '處理失敗，詳見下方的狀態列' : `${stages.length} 個階段`}
          </DialogDescription>
        </DialogHeader>
        <ol className="divide-y rounded-md border text-sm">
          {stages.map((stage, i) => (
            <li key={stage.name} className="flex items-center gap-2 px-3 py-2">
              <span className="w-4 shrink-0 text-right text-xs text-muted-foreground tabular-nums">{i + 1}</span>
              <StageRunIcon status={stage.status} />
              <span className="min-w-0 flex-1 truncate" title={stage.name}>
                {stage.name}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                {stage.status === 'running'
                  ? detail || '處理中…'
                  : stage.status === 'done'
                    ? stage.rows === null
                      ? '完成'
                      : `${stage.rows.toLocaleString()} 列`
                    : stage.status === 'error'
                      ? '失敗'
                      : '等待中'}
              </span>
            </li>
          ))}
        </ol>
        {result ? <p className="text-sm break-all">{result}</p> : null}
        <DialogFooter>
          <Button onClick={onClose} disabled={busy}>
            {busy ? '處理中…' : '關閉'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
