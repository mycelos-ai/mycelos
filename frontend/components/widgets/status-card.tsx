import type { StatusCardData } from '@/lib/types'

const styleColors: Record<string, string> = {
  info: 'border-blue-500',
  success: 'border-green-500',
  warning: 'border-yellow-500',
  error: 'border-red-500',
}

export function StatusCardWidget({ data }: { data: StatusCardData }) {
  return (
    <div className={`my-2 rounded-lg border-l-4 ${styleColors[data.style]} bg-[var(--border)]/30 p-4`}>
      <h3 className="font-semibold mb-2">{data.title}</h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {Object.entries(data.facts).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-[var(--muted)]">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
