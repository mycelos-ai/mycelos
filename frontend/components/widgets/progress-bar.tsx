import type { ProgressBarData } from '@/lib/types'

export function ProgressBarWidget({ data }: { data: ProgressBarData }) {
  const pct = data.total > 0 ? (data.current / data.total) * 100 : 0

  return (
    <div className="my-2">
      <div className="flex justify-between text-sm mb-1">
        <span>{data.label}</span>
        <span className="text-[var(--muted)]">{pct.toFixed(0)}%</span>
      </div>
      <div className="h-2 rounded-full bg-[var(--border)] overflow-hidden">
        <div
          className="h-full rounded-full bg-[var(--accent)] transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}
