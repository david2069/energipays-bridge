function dashboardTab() {
  return {
    boosting: false,

    async sendBoost(period) {
      if (this.boosting) return
      this.boosting = true
      try {
        const r = await fetch('/api/boost', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ period }),
        })
        const d = await r.json()
        if (!r.ok) {
          Alpine.store('app').addToast(d.detail || 'Boost failed', 'error')
        } else {
          Alpine.store('app').addToast(`Boost ${period}h sent`, 'info')
        }
      } catch (e) {
        Alpine.store('app').addToast('Network error', 'error')
      } finally {
        this.boosting = false
      }
    },

    fmt,
  }
}
