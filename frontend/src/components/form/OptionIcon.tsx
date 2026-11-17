/**
 * 選項卡片用的小圖（schema 的 ui_option_icons 指定名稱）。
 *
 * 文氏圖：左邊的圓是「這一張表」、右邊的圓是「另一張表」，填色是留下來的列；
 * 右邊的圓畫虛線代表不帶另一張表的欄（只拿來比對）。
 */
import { useId } from 'react'

type Venn = { fill: 'left' | 'inner' | 'left_only'; dashed: boolean }

const VENN: Record<string, Venn> = {
  venn_left: { fill: 'left', dashed: false },
  venn_inner: { fill: 'inner', dashed: false },
  venn_left_only: { fill: 'left_only', dashed: true },
  venn_inner_filter: { fill: 'inner', dashed: true },
}

const LEFT = { cx: 25, cy: 20, r: 15 }
const RIGHT = { cx: 39, cy: 20, r: 15 }

function VennIcon({ fill, dashed }: Venn) {
  const id = useId()
  const right = <circle {...RIGHT} />
  return (
    <svg viewBox="0 0 64 40" className="h-10 w-16" aria-hidden="true">
      <defs>
        <clipPath id={`${id}-right`}>{right}</clipPath>
        <mask id={`${id}-no-right`}>
          <rect width="64" height="40" fill="white" />
          <circle {...RIGHT} fill="black" />
        </mask>
      </defs>
      {fill === 'left' ? <circle {...LEFT} className="fill-primary/70" /> : null}
      {fill === 'inner' ? <circle {...LEFT} className="fill-primary/70" clipPath={`url(#${id}-right)`} /> : null}
      {fill === 'left_only' ? <circle {...LEFT} className="fill-primary/70" mask={`url(#${id}-no-right)`} /> : null}
      <circle {...LEFT} className="fill-none stroke-foreground/70" strokeWidth="1.5" />
      <circle {...RIGHT} className="fill-none stroke-foreground/70" strokeWidth="1.5" strokeDasharray={dashed ? '3 2.5' : undefined} />
    </svg>
  )
}

export function OptionIcon({ name }: { name?: string }) {
  const venn = name ? VENN[name] : undefined
  return venn ? <VennIcon {...venn} /> : null
}
