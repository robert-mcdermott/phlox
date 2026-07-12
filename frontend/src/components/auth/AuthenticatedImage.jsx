import { useEffect, useState } from 'react'
import { api } from '../../api/client'

export default function AuthenticatedImage({ url, alt = '', ...props }) {
  const [blobUrl, setBlobUrl] = useState(null)

  useEffect(() => {
    let active = true
    let created = null
    api.getBlob(url)
      .then((blob) => {
        if (!active) return
        created = URL.createObjectURL(blob)
        setBlobUrl(created)
      })
      .catch(() => { if (active) setBlobUrl(null) })
    return () => {
      active = false
      if (created) URL.revokeObjectURL(created)
    }
  }, [url])

  if (!blobUrl) return null
  return <img src={blobUrl} alt={alt} {...props} />
}
