function settingsTab() {
  return {
    pollInterval: 60,
    rawAgeDays: 7,
    retentionDays: 30,
    saving: false,

    async init() {
      const [pi, ra, rd] = await Promise.all([
        this._get('poll_interval'),
        this._get('raw_age_days'),
        this._get('retention_days'),
      ])
      if (pi) this.pollInterval = parseInt(pi) || 60
      if (ra) this.rawAgeDays = parseInt(ra) || 7
      if (rd) this.retentionDays = parseInt(rd) || 30
    },

    async _get(key) {
      try {
        const r = await fetch(`/api/config/${key}`)
        if (!r.ok) return null
        return (await r.json()).value
      } catch (_) { return null }
    },

    async _put(key, value) {
      return fetch(`/api/config/${key}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value }),
      })
    },

    async toggleSafeMode() {
      const next = !Alpine.store('app').safeMode
      const r = await this._put('safe_mode', next ? '1' : '0')
      if (r.ok) {
        Alpine.store('app').safeMode = next
        Alpine.store('app').addToast(`Safe Mode ${next ? 'enabled' : 'disabled'}`)
      }
    },

    async savePollInterval() {
      this.saving = true
      try {
        await this._put('poll_interval', String(this.pollInterval))
        Alpine.store('app').addToast('Poll interval saved — restart to apply')
      } finally { this.saving = false }
    },

    async saveRetention() {
      this.saving = true
      try {
        await Promise.all([
          this._put('raw_age_days', String(this.rawAgeDays)),
          this._put('retention_days', String(this.retentionDays)),
        ])
        Alpine.store('app').addToast('Retention settings saved')
      } finally { this.saving = false }
    },
  }
}
