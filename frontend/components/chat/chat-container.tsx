'use client'

import { useEffect, useRef, useState } from 'react'
import { useChatStream } from '@/lib/use-chat-stream'
import { ChatInput } from './chat-input'
import { ChatMessageBubble } from './chat-message'
import { ThinkingIndicator } from './thinking-indicator'

interface AgentInfo {
  name: string
  display_name: string | null
}

export function ChatContainer() {
  const { messages, isStreaming, sendMessage } = useChatStream()
  const scrollRef = useRef<HTMLDivElement>(null)
  const [agentInfo, setAgentInfo] = useState<AgentInfo | null>(null)

  // Fetch the main agent's display name
  useEffect(() => {
    const apiBase = process.env.NEXT_PUBLIC_API_URL?.replace('/api/chat', '') || ''
    fetch(`${apiBase}/api/agents/mycelos`)
      .then((r) => r.json())
      .then((data) => setAgentInfo({ name: data.name || 'Mycelos', display_name: data.display_name }))
      .catch(() => setAgentInfo({ name: 'Mycelos', display_name: null }))
  }, [])

  const displayName = agentInfo?.display_name || agentInfo?.name || 'Mycelos'

  // Show thinking when streaming but no content yet
  const lastMessage = messages[messages.length - 1]
  const isThinking = isStreaming && lastMessage?.role === 'assistant' && !lastMessage.content && !lastMessage.widgets?.length

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, isThinking])

  return (
    <div className="flex flex-col h-screen">
      <header className="border-b border-[var(--border)] px-6 py-4">
        <h1 className="text-lg font-semibold">{displayName}</h1>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        <div className="max-w-3xl mx-auto space-y-4">
          {messages.length === 0 && (
            <div className="text-center text-[var(--muted)] py-20">
              <p className="text-2xl mb-2">{displayName}</p>
              <p className="text-sm">Send a message to get started.</p>
            </div>
          )}
          {messages.map((msg, i) => (
            <ChatMessageBubble
              key={msg.id}
              message={msg}
              isStreaming={isStreaming && i === messages.length - 1 && msg.role === 'assistant'}
              systemName={agentInfo?.name}
            />
          ))}
          {isThinking && <ThinkingIndicator />}
        </div>
      </div>

      <ChatInput onSend={sendMessage} disabled={isStreaming} agentName={displayName} />
    </div>
  )
}
