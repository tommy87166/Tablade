/**
 * 狀態列（與進度視窗）上的用量：[記憶體圖示] 主程序＋轉碼 [磁碟圖示] 暫存資料夾。
 * 滑鼠移上去分開寫主程序（Polars）、轉碼各用了多少（目前、這次開啟後最高），與暫存資料夾用量。
 * 數字由 Python 每 2 秒推送（bridge.ts 的 onUsage）；記憶體讀不到的作業系統只顯示暫存。
 */
import { HardDrive, MemoryStick } from 'lucide-react'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { Usage } from '../bridge'
import { formatSize } from '../lib/format'

const withPeak = (current: number, peak: number | null) => `${formatSize(current)}（最高 ${formatSize(peak ?? current)}）`

export function UsageStatus({ usage, className = '' }: { usage: Usage; className?: string }) {
  const memory = usage.memory !== null ? usage.memory + (usage.workers ?? 0) : null
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={`flex shrink-0 items-center gap-2 text-muted-foreground tabular-nums ${className}`}>
          {memory !== null ? (
            <span className="flex items-center gap-1" aria-label={`記憶體 ${formatSize(memory)}`}>
              <MemoryStick className="size-3.5" />
              {formatSize(memory)}
            </span>
          ) : null}
          <span className="flex items-center gap-1" aria-label={`暫存資料夾 ${formatSize(usage.staging)}`}>
            <HardDrive className="size-3.5" />
            {formatSize(usage.staging)}
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-80 flex-col items-start gap-1">
        {usage.memory !== null ? (
          <div>
            <div>記憶體用量</div>
            <ul className="list-disc pl-4">
              <li>主程序（Polars）：{withPeak(usage.memory, usage.peak)}</li>
              <li>轉碼：{withPeak(usage.workers ?? 0, usage.workers_peak)}</li>
            </ul>
          </div>
        ) : null}
        <div>暫存資料夾用量：{formatSize(usage.staging)}</div>
      </TooltipContent>
    </Tooltip>
  )
}
