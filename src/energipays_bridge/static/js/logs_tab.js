function logsTab() {
  return {
    logs: [],
    filter: '',
    search: '',
    loading: false,
    autoRefresh: true,
    page: 1,
    pageSize: 50,
    _timer: null,

    get filteredLogs() {
      let result = this.logs
      if (this.filter) result = result.filter(l => l.level === this.filter)
      const q = this.search.trim().toLowerCase()
      if (q) result = result.filter(l => l.msg.toLowerCase().includes(q) || l.name.toLowerCase().includes(q))
      return result
    },

    get totalPages() {
      return Math.max(1, Math.ceil(this.filteredLogs.length / this.pageSize))
    },

    get pagedLogs() {
      const start = (this.page - 1) * this.pageSize
      return this.filteredLogs.slice(start, start + this.pageSize)
    },

    get pageInfo() {
      const total = this.filteredLogs.length
      if (!total) return '0 entries'
      const start = (this.page - 1) * this.pageSize + 1
      const end = Math.min(this.page * this.pageSize, total)
      return `${start}–${end} of ${total}`
    },

    resetPage() { this.page = 1 },
    prevPage()  { if (this.page > 1) this.page-- },
    nextPage()  { if (this.page < this.totalPages) this.page++ },

    async init() {
      this.$watch('filter', () => this.resetPage())
      this.$watch('search', () => this.resetPage())
      await this.refresh()
      this._scheduleRefresh()
    },

    _scheduleRefresh() {
      if (this._timer) clearInterval(this._timer)
      this._timer = setInterval(() => {
        if (this.autoRefresh && Alpine.store('app').activeTab === 'logs') this.refresh()
      }, 5000)
    },

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

    clear() {
      this.logs = []
    },
  }
}
