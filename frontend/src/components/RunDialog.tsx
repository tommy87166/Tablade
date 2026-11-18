/**
 * 處理中的進度視窗（處理並匯出資料、載入資料及預覽、完整跑一次）：所有進度都只顯示在這裡。
 *
 * - 最上面是目前在做什麼（轉碼哪個檔、處理到第幾行、檢查警告第幾項），有百分比時有進度條。
 * - 下面一列一列：匯出時先是「準備檔案」「試跑」，再來是每個階段。等待中是灰點、處理中轉圈、
 *   完成打勾、失敗紅叉；處理中那一列的下面寫細節（已處理幾個檔、試跑到第幾個階段）。
 * - 匯出跑完後留著當結果摘要，按「關閉」才收起來；試跑出錯時可以「前往這一步」。
 *
 * 狀態由 Python 推送（bridge.ts 的 onRun、onProgress）。背景不模糊（還是不能點：Python 正在忙）。
 */
import { Check, Loader2, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { Progress, RunState, StageRun } from '../bridge'
import { formatAmount, formatElapsed } from '../lib/format'

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
  run: RunState
  progress: Progress | null
  busy: boolean
  /** 跑完後的摘要（例如匯出到哪裡、幾列） */
  result?: string
  failed?: boolean
  /** 出錯的說明；有 onGo 時多一個「前往這一步」 */
  error?: { message: string; onGo?: () => void }
  onClose: () => void
}

interface Row {
  key: string
  number?: number
  name: string
  status: StageRun['status']
  detail: string
  rows?: number | null
}

/** 進度的說法去掉「階段「X」：」（每一列已經寫了是哪個階段）。 */
const activity = (stage: string) => stage.replace(/^階段「[^」]*」：/, '')

function statusText(row: Row) {
  if (row.status === 'running') return '處理中…'
  if (row.status === 'error') return '失敗'
  if (row.status === 'waiting') return '等待中'
  if (row.rows !== undefined && row.rows !== null) return `${row.rows.toLocaleString()} 列`
  return '完成'
}

export function RunDialog({ title, run, progress, busy, result, failed, error, onClose }: Props) {
  const [start] = useState(() => Date.now())
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!busy) return
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [busy, start])

  const rows: Row[] = [
    ...run.phases.map((p) => ({ key: `phase:${p.name}`, name: p.name, status: p.status, detail: p.detail })),
    ...run.stages.map((s, i) => ({
      key: `stage:${s.name}`,
      number: i + 1,
      name: s.name,
      status: s.status,
      detail: s.detail,
      rows: s.rows,
    })),
  ]
  const ratio = progress && progress.total > 0 ? Math.min(progress.done / progress.total, 1) : null
  const done = run.stages.filter((s) => s.status === 'done').length
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-lg" showCloseButton={!busy} overlayClassName="supports-backdrop-filter:backdrop-blur-none">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {busy
              ? `階段依序執行：${done} / ${run.stages.length} 完成・已經過 ${formatElapsed(elapsed)}`
              : failed
                ? '處理失敗'
                : `完成・共 ${formatElapsed(elapsed)}`}
          </DialogDescription>
        </DialogHeader>
        {busy && progress && progress.stage ? (
          <div className="space-y-1 rounded-md bg-primary/5 px-3 py-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="font-medium">{activity(progress.stage)}</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">{progress.file}</span>
              <span className="shrink-0 text-muted-foreground tabular-nums">
                {ratio !== null ? `${formatAmount(progress)}（${Math.round(ratio * 100)}%）` : '處理中…'}
              </span>
            </div>
            <div className="h-1 overflow-hidden rounded bg-primary/15">
              <div
                className={`h-full bg-primary transition-[width] ${ratio === null ? 'w-1/3 animate-pulse' : ''}`}
                style={ratio === null ? undefined : { width: `${ratio * 100}%` }}
              />
            </div>
          </div>
        ) : null}
        <ol className="divide-y rounded-md border text-sm">
          {rows.map((row) => (
            <li key={row.key} className="px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="w-4 shrink-0 text-right text-xs text-muted-foreground tabular-nums">{row.number ?? ''}</span>
                <StageRunIcon status={row.status} />
                <span className={`min-w-0 flex-1 truncate ${row.number ? '' : 'text-muted-foreground'}`} title={row.name}>
                  {row.name}
                </span>
                <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{statusText(row)}</span>
              </div>
              {row.detail ? <div className="mt-0.5 truncate pl-10 text-xs text-muted-foreground">{row.detail}</div> : null}
            </li>
          ))}
        </ol>
        {error ? (
          <div className="space-y-2 rounded-md bg-destructive/10 p-2 text-sm text-destructive">
            <p className="break-all">{error.message}</p>
            {error.onGo ? (
              <Button size="sm" variant="outline" onClick={error.onGo}>
                前往這一步
              </Button>
            ) : null}
          </div>
        ) : null}
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
