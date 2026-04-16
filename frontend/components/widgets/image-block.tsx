import type { ImageBlockData } from '@/lib/types'

export function ImageBlockWidget({ data }: { data: ImageBlockData }) {
  return (
    <figure className="my-2">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={data.url} alt={data.alt} className="rounded-lg max-w-full" />
      {data.caption && (
        <figcaption className="text-sm text-[var(--muted)] mt-1">{data.caption}</figcaption>
      )}
    </figure>
  )
}
