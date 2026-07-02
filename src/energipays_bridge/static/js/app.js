// Alpine global store — shared state + polling loop
document.addEventListener('alpine:initialized', () => {
  // Sync URL hash whenever activeTab changes (Alpine.effect works on stores)
  Alpine.effect(() => { location.hash = Alpine.store('app').activeTab || '' })
})

document.addEventListener('alpine:init', () => {
  Alpine.store('dashboard', { weatherNem: { weather: null, nem: null } })

  Alpine.store('app', {
    activeTab: 'dashboard',
    points: {},
    _boostPendingTs: 0,   // ms timestamp when boost was sent; 0 = not pending
    connected: false,
    isDark: false,
    moreOpen: false,
    lastPollTs: 0,
    safeMode: false,
    navCfg: {temp:1,grid:1,solar:1,heater:1,wifi:1,lora:1},
    _prevConnected: null,   // tracks last known state for transition toasts
    deviceId: '',
    selectedDeviceId: '',
    devices: [],
    dataServer: '',

    mqttEnabled: false,

    // All tabs — filtered so MQTT only appears when MQTT is enabled
    _allTabs: [
      { id: 'dashboard', label: 'Home',     icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/></svg>' },
      { id: 'analytics', label: 'Analytics', icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>' },
      { id: 'rules',    label: 'Rules',    icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>' },
      { id: 'raw',      label: 'Raw',      icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>' },
      { id: 'mqtt',     label: 'MQTT',     icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8.111 16.404a5.5 5.5 0 017.778 0M12 20h.01m-7.08-7.071c3.904-3.905 10.236-3.905 14.141 0M1.394 9.393c5.857-5.857 15.355-5.857 21.213 0"/></svg>' },
      { id: 'settings', label: 'Settings', icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>' },
      { id: 'logs',     label: 'Logs',     icon: '<svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>' },
    ],
    get tabs() { return this._allTabs.filter(t => t.id !== 'mqtt' || this.mqttEnabled) },
    // Mobile bottom nav: primary tabs shown in bar
    get mainTabs() { return this._allTabs.filter(t => ['dashboard','analytics','rules','settings'].includes(t.id)) },
    // Mobile "More" sheet: secondary tabs (MQTT only if enabled)
    get moreTabs() { return this._allTabs.filter(t => ['raw','logs'].includes(t.id) || (t.id === 'mqtt' && this.mqttEnabled)) },

    toasts: [],

    get lastPollLabel() {
      if (!this.lastPollTs) return 'never'
      const ago = Math.round((Date.now() / 1000) - this.lastPollTs)
      if (ago < 5) return 'just now'
      if (ago < 60) return `${ago}s ago`
      return `${Math.round(ago / 60)}m ago`
    },

    batteryLabel: '',
    needsSetup: false,
    setupModal: false,
    userName: '',
    userEmail: '',
    deviceName: '',
    deviceTimezone: '',
    deviceLat: null,
    deviceLon: null,
    userAddress: '',
    userCity: '',
    userState: '',
    userCountry: '',

    init() {
      // Restore tab from URL hash on load
      const validTabs = this.tabs.map(t => t.id)
      const hash = location.hash.slice(1)
      if (hash && validTabs.includes(hash)) this.activeTab = hash

      // Handle browser back/forward
      window.addEventListener('hashchange', () => {
        const h = location.hash.slice(1)
        if (h && validTabs.includes(h)) this.activeTab = h
      })

      // Nav stats config
      const navSaved = localStorage.getItem('navStats')
      if (navSaved) { try { const p = JSON.parse(navSaved); if (p) this.navCfg = Object.assign(this.navCfg, p) } catch(_) {} }

      // Dark mode
      const saved = localStorage.getItem('theme')
      this.isDark = saved ? saved === 'dark' : true
      document.documentElement.classList.toggle('dark', this.isDark)

      this._fetchPoints()
      this._loadDevices()
      setInterval(() => this._fetchPoints(), 10000)
      // Load battery integration name for SVG label
      fetch('/api/integrations').then(r => r.ok ? r.json() : null).then(d => {
        const batt = (d || []).find(i => i.type === 'battery' && i.enabled)
        if (batt) this.batteryLabel = batt.name
      }).catch(() => {})
      // Load MQTT enabled state to show/hide MQTT tab
      fetch('/api/mqtt/config').then(r => r.ok ? r.json() : null).then(d => {
        if (d) this.mqttEnabled = d.enabled && !d.paused
      }).catch(() => {})
    },

    async _loadDevices() {
      try {
        const r = await fetch('/api/device/list')
        if (!r.ok) return
        const d = await r.json()
        const list = Array.isArray(d) ? d : (d.data || [])
        this.devices = list
        if (!this.selectedDeviceId && list.length)
          this.selectedDeviceId = list[0].id
      } catch (_) {}
    },

    async switchDevice(id) {
      if (id === this.selectedDeviceId) return
      try {
        const r = await fetch('/api/device/switch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ device_id: id }),
        })
        const d = await r.json()
        if (r.ok) {
          this.selectedDeviceId = id
          this.deviceId = id
          this.deviceName = d.device_name || id
          this.addToast(`Switched to ${d.device_name || id}`, 'info')
        } else {
          this.addToast(d.detail || 'Switch failed', 'error')
        }
      } catch (_) {
        this.addToast('Network error', 'error')
      }
    },

    async _fetchPoints() {
      try {
        const r = await fetch('/api/points/latest')
        if (!r.ok) return
        const d = await r.json()
        const fresh = d.points || {}
        // If boost was sent <90s ago and device hasn't confirmed yet, preserve
        // the optimistic boostStatus=true without letting it bleed into other keys.
        if (this._boostPendingTs && !fresh.boostStatus) {
          if (Date.now() - this._boostPendingTs < 90000) {
            fresh.boostStatus = true
          } else {
            this._boostPendingTs = 0
          }
        } else if (fresh.boostStatus) {
          this._boostPendingTs = 0
        }
        // Only replace points when we have real data — an empty response
        // (failed cloud poll, session expiry) should not wipe the last known values.
        if (Object.keys(fresh).length > 0) this.points = fresh
        // Notify dashboard tab so it can anchor its client-side countdown
        const dash = Alpine.store('dashboard')
        if (dash && dash.onPointsUpdate) dash.onPointsUpdate(fresh)
        const wasConnected = this._prevConnected
        this.connected = d.connected || false
        this.lastPollTs = d.last_poll_ts || 0
        // Fire transition toasts (skip the very first poll — no prior state)
        if (wasConnected !== null && wasConnected !== this.connected) {
          if (this.connected) {
            this.addToast('Bridge reconnected', 'success')
          } else {
            this.addToast('Bridge disconnected — retrying…', 'error')
          }
        }
        this._prevConnected = this.connected
        // safe_mode removed
        if (d.device_id) { this.deviceId = d.device_id; this.selectedDeviceId = d.device_id }
        if (d.user_name) this.userName = d.user_name
        if (d.user_email) this.userEmail = d.user_email
        if (d.device_name) this.deviceName = d.device_name
        if (d.device_timezone) this.deviceTimezone = d.device_timezone
        if (d.device_lat != null) this.deviceLat = d.device_lat
        if (d.device_lon != null) this.deviceLon = d.device_lon
        if (d.user_address) this.userAddress = d.user_address
        if (d.user_city) this.userCity = d.user_city
        if (d.user_state) this.userState = d.user_state
        if (d.user_country) this.userCountry = d.user_country
        this.needsSetup = d.needs_setup || false
        if (this.needsSetup && !this.setupModal) this.setupModal = true
      } catch (_) {}
    },

    addToast(msg, level = 'info') {
      const t = { msg, level, id: Date.now() + Math.random() }
      this.toasts.push(t)
      // errors must be manually dismissed; others auto-dismiss
      if (level !== 'error') {
        setTimeout(() => this.dismissToast(t.id), level === 'success' ? 5000 : 8000)
      }
    },

    dismissToast(id) {
      this.toasts = this.toasts.filter(x => x.id !== id)
    },

    setNavCfg(key, val) {
      this.navCfg[key] = val
      localStorage.setItem('navStats', JSON.stringify(this.navCfg))
    },

    toggleDark() {
      this.isDark = !this.isDark
      document.documentElement.classList.toggle('dark', this.isDark)
      localStorage.setItem('theme', this.isDark ? 'dark' : 'light')
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
