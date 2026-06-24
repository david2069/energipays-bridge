function rulesTab() {
  return {
    rules: [],
    loading: false,
    expanded: null,

    async init() { await this.refresh() },

    async refresh() {
      this.loading = true
      try {
        const r = await fetch('/api/rules')
        if (!r.ok) return
        const d = await r.json()
        this.rules = Array.isArray(d) ? d : (d.data || [])
      } catch (_) {
        Alpine.store('app').addToast('Failed to load rules', 'error')
      } finally {
        this.loading = false
      }
    },

    toggle(id) { this.expanded = this.expanded === id ? null : id },
  }
}
