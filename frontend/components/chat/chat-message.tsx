'use client'

import type { ChatMessage } from '@/lib/types'
import { WidgetRenderer } from '@/components/widgets/widget-renderer'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface ChatMessageProps {
  message: ChatMessage
  isStreaming?: boolean
  systemName?: string
}

export function ChatMessageBubble({ message, isStreaming, systemName }: ChatMessageProps) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm ${
          isUser
            ? 'bg-[var(--accent)] text-white'
            : 'bg-[var(--border)]/50'
        }`}
      >
        {/* Agent label — show custom name with system name in parentheses */}
        {!isUser && message.agent && (
          <div className="text-xs text-[var(--muted)] mb-1">
            {message.agent}
            {systemName && message.agent !== systemName && (
              <span className="opacity-60"> ({systemName})</span>
            )}
          </div>
        )}

        {/* Text content */}
        {message.content && (
          <div className="break-words">
            {isUser ? (
              <span className="whitespace-pre-wrap">{message.content}</span>
            ) : (
              <div className="prose prose-invert prose-sm max-w-none [&_pre]:bg-[#1e1e1e] [&_pre]:rounded [&_pre]:p-3 [&_code]:text-[var(--accent)] [&_a]:text-[var(--accent)] [&_table]:border-collapse [&_th]:border [&_th]:border-[var(--border)] [&_th]:px-3 [&_th]:py-1 [&_td]:border [&_td]:border-[var(--border)] [&_td]:px-3 [&_td]:py-1 [&_hr]:border-[var(--border)]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {message.content}
                </ReactMarkdown>
              </div>
            )}
            {isStreaming && (
              <span className="inline-block w-2 h-4 bg-[var(--foreground)] animate-pulse ml-0.5" />
            )}
          </div>
        )}

        {/* Widgets */}
        {message.widgets?.map((widget, i) => (
          <div key={i} className="mt-3">
            <WidgetRenderer widget={widget} />
          </div>
        ))}

        {/* Metadata */}
        {!isUser && message.tokens && message.tokens > 0 && !isStreaming && (
          <div className="text-xs text-[var(--muted)] mt-2">
            {message.tokens} tokens · {message.model}
            {message.cost ? ` · $${message.cost.toFixed(4)}` : ''}
          </div>
        )}
      </div>
    </div>
  )
}
