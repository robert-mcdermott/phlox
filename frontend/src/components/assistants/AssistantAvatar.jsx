// Renders an assistant's avatar: uploaded image (data URL), emoji, or colored initials.
const PALETTE = [
  'bg-rose-500', 'bg-orange-500', 'bg-amber-500', 'bg-emerald-500',
  'bg-teal-500', 'bg-sky-500', 'bg-indigo-500', 'bg-violet-500', 'bg-fuchsia-500',
]

function initials(name) {
  const words = (name || '').trim().split(/\s+/).filter(Boolean)
  if (!words.length) return '?'
  return words.slice(0, 2).map((w) => w[0].toUpperCase()).join('')
}

function colorFor(name) {
  let hash = 0
  for (const ch of name || '') hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return PALETTE[hash % PALETTE.length]
}

export default function AssistantAvatar({ assistant, size = 32, className = '' }) {
  const px = { width: size, height: size }
  const name = assistant?.name || ''
  const avatar = assistant?.avatar

  if (avatar && avatar.startsWith('data:image/')) {
    return (
      <img
        src={avatar}
        alt={name}
        style={px}
        className={`shrink-0 rounded-full object-cover ${className}`}
      />
    )
  }
  if (avatar) {
    return (
      <span
        style={{ ...px, fontSize: size * 0.6 }}
        className={`flex shrink-0 items-center justify-center rounded-full bg-surface-2 leading-none ${className}`}
        aria-label={name}
      >
        {avatar}
      </span>
    )
  }
  return (
    <span
      style={{ ...px, fontSize: size * 0.42 }}
      className={`flex shrink-0 items-center justify-center rounded-full font-semibold text-white ${colorFor(name)} ${className}`}
      aria-label={name}
    >
      {initials(name)}
    </span>
  )
}
