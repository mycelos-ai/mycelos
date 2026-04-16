'use client'

import { useState } from 'react'
import type { ActionConfirmData } from '@/lib/types'

interface ActionConfirmProps {
  data: ActionConfirmData
  onAction?: (command: string) => void
}

export function ActionConfirm({ data, onAction }: ActionConfirmProps) {
  const [command, setCommand] = useState(data.command)
  const [editing, setEditing] = useState(false)
  const [executed, setExecuted] = useState(false)
  const [declined, setDeclined] = useState(false)

  const handleExecute = () => {
    setExecuted(true)
    onAction?.(command)
  }

  const handleDecline = () => {
    setDeclined(true)
    onAction?.('__declined__')
  }

  if (executed) {
    return (
      <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-3 my-2">
        <div className="text-sm text-green-400">Executed: {command}</div>
      </div>
    )
  }

  if (declined) {
    return (
      <div className="rounded-lg border border-zinc-500/30 bg-zinc-500/5 p-3 my-2">
        <div className="text-sm text-zinc-400">Declined</div>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/5 p-3 my-2">
      <div className="text-xs font-medium text-yellow-500 mb-2">Action Approval</div>

      {editing ? (
        <input
          type="text"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              setEditing(false)
              handleExecute()
            }
            if (e.key === 'Escape') setEditing(false)
          }}
          className="w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-1.5 text-sm font-mono text-white focus:outline-none focus:border-yellow-500"
          autoFocus
        />
      ) : (
        <div
          className="font-mono text-sm text-white bg-zinc-900 rounded px-3 py-1.5 cursor-pointer hover:bg-zinc-800"
          onClick={() => data.editable && setEditing(true)}
          title={data.editable ? 'Click to edit' : undefined}
        >
          {command}
        </div>
      )}

      {data.reason && (
        <div className="text-xs text-zinc-400 mt-1.5">{data.reason}</div>
      )}

      <div className="flex gap-2 mt-3">
        <button
          onClick={handleExecute}
          className="px-3 py-1 text-xs font-medium rounded bg-yellow-600 hover:bg-yellow-500 text-white transition-colors"
        >
          Execute
        </button>
        {data.editable && !editing && (
          <button
            onClick={() => setEditing(true)}
            className="px-3 py-1 text-xs font-medium rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-colors"
          >
            Edit
          </button>
        )}
        <button
          onClick={handleDecline}
          className="px-3 py-1 text-xs font-medium rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-400 transition-colors"
        >
          Decline
        </button>
      </div>
    </div>
  )
}
