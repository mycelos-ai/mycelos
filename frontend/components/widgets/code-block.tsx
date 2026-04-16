import type { CodeBlockData } from '@/lib/types'

export function CodeBlockWidget({ data }: { data: CodeBlockData }) {
  return (
    <div className="my-2 relative group">
      <div className="flex items-center justify-between px-4 py-1 bg-[#1e1e1e] rounded-t text-xs text-[var(--muted)]">
        <span>{data.language}</span>
      </div>
      <pre className="bg-[#1e1e1e] rounded-b p-4 overflow-x-auto text-sm">
        <code>{data.code}</code>
      </pre>
    </div>
  )
}
