import type { ChoiceBoxData } from '@/lib/types'

export function ChoiceBoxWidget({ data }: { data: ChoiceBoxData }) {
  return (
    <div className="my-2">
      <p className="font-semibold mb-2">{data.prompt}</p>
      <div className="flex flex-wrap gap-2">
        {data.options.map((opt) => (
          <button
            key={opt.id}
            className="px-4 py-2 rounded-lg border border-[var(--border)] hover:border-[var(--accent)] hover:bg-[var(--accent)]/10 transition-colors text-sm"
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  )
}
