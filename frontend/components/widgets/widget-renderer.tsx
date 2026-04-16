import type { WidgetData } from '@/lib/types'
import { TextBlockWidget } from './text-block'
import { DataTableWidget } from './data-table'
import { StatusCardWidget } from './status-card'
import { ProgressBarWidget } from './progress-bar'
import { CodeBlockWidget } from './code-block'
import { ChoiceBoxWidget } from './choice-box'
import { ConfirmWidget } from './confirm-dialog'
import { ActionConfirm } from './action-confirm'
import { ImageBlockWidget } from './image-block'
import { ComposeWidget } from './compose'

export function WidgetRenderer({ widget }: { widget: WidgetData }) {
  switch (widget.type) {
    case 'text_block':
      return <TextBlockWidget data={widget} />
    case 'table':
      return <DataTableWidget data={widget} />
    case 'status_card':
      return <StatusCardWidget data={widget} />
    case 'progress_bar':
      return <ProgressBarWidget data={widget} />
    case 'code_block':
      return <CodeBlockWidget data={widget} />
    case 'choice_box':
      return <ChoiceBoxWidget data={widget} />
    case 'confirm':
      return <ConfirmWidget data={widget} />
    case 'action_confirm':
      return <ActionConfirm data={widget} />
    case 'image_block':
      return <ImageBlockWidget data={widget} />
    case 'compose':
      return <ComposeWidget data={widget} />
    default:
      return <pre className="text-xs text-[var(--muted)]">{JSON.stringify(widget, null, 2)}</pre>
  }
}
