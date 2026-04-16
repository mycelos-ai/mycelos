import type { TableData } from '@/lib/types'

export function DataTableWidget({ data }: { data: TableData }) {
  return (
    <div className="overflow-x-auto my-2">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-[var(--border)]">
            {data.headers.map((h, i) => (
              <th key={i} className="text-left py-2 px-3 font-semibold text-[var(--muted)]">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, ri) => (
            <tr key={ri} className="border-b border-[var(--border)] last:border-0">
              {row.map((cell, ci) => (
                <td key={ci} className="py-2 px-3">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
