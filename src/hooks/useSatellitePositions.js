import { useEffect, useState } from 'react'
import { getCurrentPositions } from '../api/tleApi.js'
import { useGlobeStore } from '../store/useGlobeStore.js'

export function useSatellitePositions() {
  const [loading, setLoading] = useState(true)
  const setSatellitePositions = useGlobeStore((s) => s.setSatellitePositions)

  useEffect(() => {
    let active = true

    const fetchPositions = async () => {
      try {
        const positions = await getCurrentPositions()
        if (active) {
          setSatellitePositions(positions)
          setLoading(false)
        }
      } catch (err) {
        console.error('Failed to pull current propagated positions:', err)
        if (active) {
          setLoading(false)
        }
      }
    }

    fetchPositions()

    const intervalId = setInterval(fetchPositions, 30000)

    return () => {
      active = false
      clearInterval(intervalId)
    }
  }, [setSatellitePositions])

  return { loading }
}
