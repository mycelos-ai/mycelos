import type { ComposeData } from '@/lib/types'
import { WidgetRenderer } from './widget-renderer'

export function ComposeWidget({ data }: { data: ComposeData }) {
  return (
    <div className="space-y-2">
      {data.children.map((child, i) => (
        <WidgetRenderer key={i} widget={child} />
      ))}
    </div>
  )
}
