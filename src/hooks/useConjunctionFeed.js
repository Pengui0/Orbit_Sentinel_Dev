import { useEffect, useState } from 'react'
import { getActiveConjunctions, getConjunctionStats } from '../api/conjunctionApi.js'
import { useConjunctionStore } from '../store/useConjunctionStore.js'
import { useSystemStore } from '../store/useSystemStore.js'

export function useConjunctionFeed() {
  const [loading, setLoading] = useState(true)
  const setConjunctions = useConjunctionStore((s) => s.setConjunctions)
  const setActiveConjunctionCount = useSystemStore((s) => s.setActiveConjunctionCount)

  useEffect(() => {
    let active = true

    const fetchFeedAndStats = async () => {
      try {
        const [conjunctions, stats] = await Promise.all([
          getActiveConjunctions(),
          getConjunctionStats()
        ])
        
        if (active) {
          setConjunctions(conjunctions)
          if (stats) {
            if (stats.unresolved_total !== undefined) {
              setActiveConjunctionCount(stats.unresolved_total)
            } else if (conjunctions) {
              setActiveConjunctionCount(conjunctions.length)
            }
          } else if (conjunctions) {
            setActiveConjunctionCount(conjunctions.length)
          }
          setLoading(false)
        }
      } catch (err) {
        console.error('Failed to update conjunction feed data stream:', err)
        if (active) {
          setLoading(false)
        }
      }
    }

    fetchFeedAndStats()

    const intervalId = setInterval(fetchFeedAndStats, 60000)

    return () => {
      active = false
      clearInterval(intervalId)
    }
  }, [setConjunctions, setActiveConjunctionCount])

  return { loading }
}
