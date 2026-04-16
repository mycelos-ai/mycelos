// --- Chat Events (mirrors src/mycelos/chat/events.py) ---

export type ChatEventType =
  | 'agent'
  | 'text-delta'
  | 'text'
  | 'plan'
  | 'step-progress'
  | 'system-response'
  | 'error'
  | 'done'
  | 'session'
  | 'widget'

export interface ChatEvent {
  type: ChatEventType
  data: Record<string, unknown>
}

export interface AgentEvent extends ChatEvent {
  type: 'agent'
  data: { agent: string }
}

export interface TextDeltaEvent extends ChatEvent {
  type: 'text-delta'
  data: { delta: string }
}

export interface TextEvent extends ChatEvent {
  type: 'text'
  data: { content: string }
}

export interface DoneEvent extends ChatEvent {
  type: 'done'
  data: { tokens: number; model: string; cost: number }
}

export interface ErrorEvent extends ChatEvent {
  type: 'error'
  data: { message: string }
}

export interface WidgetEvent extends ChatEvent {
  type: 'widget'
  data: { widget: WidgetData }
}

export interface SessionEvent extends ChatEvent {
  type: 'session'
  data: { session_id: string; resumed: boolean }
}

// --- Widget IR (mirrors src/mycelos/widgets/types.py) ---

export type WidgetType =
  | 'text_block'
  | 'table'
  | 'choice_box'
  | 'status_card'
  | 'progress_bar'
  | 'code_block'
  | 'confirm'
  | 'action_confirm'
  | 'image_block'
  | 'compose'

export interface TextBlockData {
  type: 'text_block'
  text: string
  weight: 'normal' | 'bold' | 'italic'
}

export interface TableData {
  type: 'table'
  headers: string[]
  rows: string[][]
}

export interface ChoiceData {
  id: string
  label: string
}

export interface ChoiceBoxData {
  type: 'choice_box'
  prompt: string
  options: ChoiceData[]
}

export interface StatusCardData {
  type: 'status_card'
  title: string
  facts: Record<string, string>
  style: 'info' | 'success' | 'warning' | 'error'
}

export interface ProgressBarData {
  type: 'progress_bar'
  label: string
  current: number
  total: number
}

export interface CodeBlockData {
  type: 'code_block'
  code: string
  language: string
}

export interface ConfirmData {
  type: 'confirm'
  prompt: string
  danger: boolean
}

export interface ActionConfirmData {
  type: 'action_confirm'
  command: string
  reason: string
  editable: boolean
}

export interface ImageBlockData {
  type: 'image_block'
  url: string
  alt: string
  caption: string | null
}

export interface ComposeData {
  type: 'compose'
  children: WidgetData[]
}

export type WidgetData =
  | TextBlockData
  | TableData
  | ChoiceBoxData
  | StatusCardData
  | ProgressBarData
  | CodeBlockData
  | ConfirmData
  | ActionConfirmData
  | ImageBlockData
  | ComposeData

// --- Chat Message (UI state) ---

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  widgets?: WidgetData[]
  agent?: string
  tokens?: number
  model?: string
  cost?: number
  timestamp: number
}
