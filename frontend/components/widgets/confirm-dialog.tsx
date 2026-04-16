import type { ConfirmData } from '@/lib/types'

export function ConfirmWidget({ data }: { data: ConfirmData }) {
  const borderColor = data.danger ? 'border-[var(--error)]' : 'border-[var(--accent)]'

  return (
    <div className={`my-2 rounded-lg border ${borderColor} p-4`}>
      <p className="mb-3">{data.prompt}</p>
      <div className="flex gap-2">
        <button className={`px-4 py-2 rounded-lg text-sm font-medium ${
          data.danger
            ? 'bg-[var(--error)] hover:bg-red-600'
            : 'bg-[var(--accent)] hover:bg-blue-600'
        } transition-colors`}>
          Yes
        </button>
        <button className="px-4 py-2 rounded-lg border border-[var(--border)] hover:bg-[var(--border)]/50 text-sm transition-colors">
          No
        </button>
      </div>
    </div>
  )
}
