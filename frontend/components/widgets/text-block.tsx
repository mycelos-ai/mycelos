import type { TextBlockData } from '@/lib/types'

export function TextBlockWidget({ data }: { data: TextBlockData }) {
  const className = {
    normal: '',
    bold: 'font-bold',
    italic: 'italic',
  }[data.weight]

  return <p className={className}>{data.text}</p>
}
