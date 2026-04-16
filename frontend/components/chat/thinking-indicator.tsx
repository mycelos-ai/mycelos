export function ThinkingIndicator() {
  return (
    <div className="flex justify-start">
      <div className="bg-[var(--border)]/50 rounded-2xl px-4 py-3 text-sm">
        <div className="flex items-center gap-1.5 text-[var(--muted)]">
          <span className="animate-bounce [animation-delay:0ms]">●</span>
          <span className="animate-bounce [animation-delay:150ms]">●</span>
          <span className="animate-bounce [animation-delay:300ms]">●</span>
          <span className="ml-2">Thinking...</span>
        </div>
      </div>
    </div>
  )
}
