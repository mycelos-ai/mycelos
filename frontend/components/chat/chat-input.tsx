'use client'

import { useState, useRef, useCallback, type KeyboardEvent } from 'react'

interface ChatInputProps {
  onSend: (message: string) => void
  disabled?: boolean
  agentName?: string
}

export function ChatInput({ onSend, disabled, agentName = 'Mycelos' }: ChatInputProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = useCallback(() => {
    const text = value.trim()
    if (!text || disabled) return
    onSend(text)
    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }, [value, disabled, onSend])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend]
  )

  return (
    <div className="border-t border-[var(--border)] p-4">
      <div className="max-w-3xl mx-auto flex gap-2">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            e.target.style.height = 'auto'
            e.target.style.height = e.target.scrollHeight + 'px'
          }}
          onKeyDown={handleKeyDown}
          placeholder={`Message ${agentName}...`}
          rows={1}
          disabled={disabled}
          className="flex-1 resize-none rounded-lg border border-[var(--border)] bg-transparent px-4 py-3 text-sm focus:outline-none focus:border-[var(--accent)] placeholder:text-[var(--muted)] disabled:opacity-50"
        />
        <button
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          className="self-end rounded-lg bg-[var(--accent)] px-4 py-3 text-sm font-medium hover:bg-blue-600 disabled:opacity-50 disabled:hover:bg-[var(--accent)] transition-colors"
        >
          Send
        </button>
      </div>
    </div>
  )
}
