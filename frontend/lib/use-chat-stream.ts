'use client'

import { useCallback, useRef, useState } from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import type { ChatMessage, WidgetData } from './types'

interface UseChatStreamOptions {
  apiUrl?: string
}

interface UseChatStreamReturn {
  messages: ChatMessage[]
  isStreaming: boolean
  sessionId: string | null
  sendMessage: (text: string) => Promise<void>
}

export function useChatStream(
  options: UseChatStreamOptions = {}
): UseChatStreamReturn {
  const { apiUrl = process.env.NEXT_PUBLIC_API_URL || '/api/chat' } = options
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const isStreamingRef = useRef(false)

  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || isStreamingRef.current) return

      // Add user message
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: text,
        timestamp: Date.now(),
      }
      setMessages((prev) => [...prev, userMsg])

      // Prepare assistant message placeholder
      const assistantId = crypto.randomUUID()
      let content = ''
      let widgets: WidgetData[] = []
      let agent = ''
      let tokens = 0
      let model = ''
      let cost = 0

      setMessages((prev) => [
        ...prev,
        {
          id: assistantId,
          role: 'assistant',
          content: '',
          timestamp: Date.now(),
        },
      ])

      isStreamingRef.current = true
      setIsStreaming(true)
      abortRef.current = new AbortController()

      try {
        await fetchEventSource(apiUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: text,
            session_id: sessionId,
            user_id: 'default',
            channel: 'web',
          }),
          signal: abortRef.current.signal,
          openWhenHidden: true,

          onmessage(ev) {
            if (!ev.data) return
            let parsed: Record<string, unknown>
            try {
              parsed = JSON.parse(ev.data)
            } catch {
              return
            }

            switch (ev.event) {
              case 'session':
                setSessionId(parsed.session_id as string)
                break

              case 'agent':
                agent = parsed.agent as string
                break

              case 'text-delta':
                content += parsed.delta as string
                break

              case 'text':
                content = parsed.content as string
                break

              case 'system-response':
                content = parsed.content as string
                break

              case 'widget':
                widgets = [...widgets, parsed.widget as WidgetData]
                break

              case 'error':
                content += `\n\nError: ${parsed.message}`
                break

              case 'done':
                tokens = (parsed.tokens as number) || 0
                model = (parsed.model as string) || ''
                cost = (parsed.cost as number) || 0
                break
            }

            // Update assistant message in place
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content,
                      widgets: widgets.length > 0 ? widgets : undefined,
                      agent: agent || undefined,
                      tokens: tokens || undefined,
                      model: model || undefined,
                      cost: cost || undefined,
                    }
                  : m
              )
            )
          },

          onerror(err) {
            console.error('SSE error:', err)
            throw err // Stop retrying
          },
        })
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          console.error('Chat stream failed:', err)
        }
      } finally {
        isStreamingRef.current = false
        setIsStreaming(false)
        abortRef.current = null
      }
    },
    [apiUrl, sessionId]
  )

  return { messages, isStreaming, sessionId, sendMessage }
}
