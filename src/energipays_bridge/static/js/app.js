// Alpine global store — shared state + polling loop
document.addEventListener('alpine:init', () => {
  Alpine.store('app', {
    activeTab: 'dashboard',
    points: {},
    connected: false,
    lastPollTs: 0,
    safeMode: true,
    deviceId: '',
    dataServer: '',

    tabs: [
      { id: 'dashboard', label: 'Home',     icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/></svg>' },
      { id: 'analytics', label: 'Analytics', icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>' },
      { id: 'rules',    label: 'Rules',    icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>' },
      { id: 'raw',      label: 'Raw',      icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>' },
      { id: 'settings', label: 'Settings', icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>' },
      { id: 'logs',     label: 'Logs',     icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>' },
    ],

    toasts: [],

    get lastPollLabel() {
      if (!this.lastPollTs) return 'never'
      const ago = Math.round((Date.now() / 1000) - this.lastPollTs)
      if (ago < 5) return 'just now'
      if (ago < 60) return `${ago}s ago`
      return `${Math.round(ago / 60)}m ago`
    },

    init() {
      this._fetchPoints()
      setInterval(() => this._fetchPoints(), 10000)
    },

    async _fetchPoints() {
      try {
        const r = await fetch('/api/points/latest')
        if (!r.ok) return
        const d = await r.json()
        this.points = d.points || {}
        this.connected = d.connected || false
        this.lastPollTs = d.last_poll_ts || 0
        if (d.safe_mode !== undefined) this.safeMode = d.safe_mode
      } catch (_) {}
    },

    addToast(msg, level = 'info') {
      const t = { msg, level }
      this.toasts.push(t)
      setTimeout(() => { this.toasts = this.toasts.filter(x => x !== t) }, 4000)
    },
  })
})

// Shared number formatter used by all tabs
function fmt(val, unit = '', decimals = 1) {
  if (val === null || val === undefined || val === '') return '—'
  const n = typeof val === 'number' ? val : parseFloat(val)
  if (isNaN(n)) return String(val)
  return n.toFixed(decimals) + (unit ? ' ' + unit : '')
}
