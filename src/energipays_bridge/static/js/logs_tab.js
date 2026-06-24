function logsTab() {
  return {
    logs: [],
    filter: '',
    loading: false,

    get filteredLogs() {
      if (!this.filter) return this.logs
      return this.logs.filter(l => l.level === this.filter)
    },

    async init() { await this.refresh() },

    async refresh() {
      this.loading = true
      try {
        const r = await fetch('/api/logs')
        if (!r.ok) return
        const d = await r.json()
        this.logs = (d.logs || []).reverse()
      } catch (_) {} finally {
        this.loading = false
      }
    },
  }
}
