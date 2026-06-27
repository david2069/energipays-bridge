const HA_TYPE_COLOR = {
  sensor:        'bg-blue-900/40 text-blue-300',
  binary_sensor: 'bg-slate-700 text-slate-400',
  switch:        'bg-green-900/40 text-green-300',
  select:        'bg-purple-900/40 text-purple-300',
  button:        'bg-amber-900/40 text-amber-300',
}

const HA_TYPE_LABEL = {
  sensor:        'sensor',
  binary_sensor: 'binary',
  switch:        'switch',
  select:        'select',
  button:        'button',
}

function mqttTab() {
  return {
    entities: [],
    loading: false,
    connected: false,
    filter: 'all',    // 'all' | 'writable' | 'sensors' | 'binary' | 'diagnostic'
    search: '',

    async init() { await this.load() },

    async load() {
      this.loading = true
      try {
        const [entR, cfgR] = await Promise.all([
          fetch('/api/mqtt/entities'),
          fetch('/api/mqtt/config'),
        ])
        if (entR.ok) this.entities = await entR.json()
        if (cfgR.ok) {
          const cfg = await cfgR.json()
          this.connected = cfg.connected
        }
      } catch (_) {}
      finally { this.loading = false }
    },

    filtered() {
      let list = this.entities
      if (this.filter === 'writable')      list = list.filter(e => e.writable)
      else if (this.filter === 'sensors')  list = list.filter(e => e.ha_type === 'sensor' && !e.diagnostic)
      else if (this.filter === 'binary')   list = list.filter(e => e.ha_type === 'binary_sensor' && !e.diagnostic)
      else if (this.filter === 'diagnostic') list = list.filter(e => e.diagnostic)
      if (this.search) {
        const q = this.search.toLowerCase()
        list = list.filter(e =>
          e.name.toLowerCase().includes(q) ||
          e.slug.toLowerCase().includes(q) ||
          (e.stat_key || '').toLowerCase().includes(q)
        )
      }
      return list
    },

    typeColor(t)  { return HA_TYPE_COLOR[t]  || 'bg-slate-700 text-slate-400' },
    typeLabel(t)  { return HA_TYPE_LABEL[t]  || t },

    valueClass(e) {
      if (e.ha_type === 'binary_sensor' || e.ha_type === 'switch') {
        return e.value === 'ON' ? 'text-green-400' : 'text-slate-500'
      }
      return 'text-slate-200'
    },

    async republish() {
      const r = await fetch('/api/mqtt/republish', { method: 'POST' })
      const d = await r.json()
      Alpine.store('app').addToast(d.detail, d.ok ? 'success' : 'error')
    },

    async unpublish() {
      if (!confirm('Remove all Energipays entities from Home Assistant?')) return
      const r = await fetch('/api/mqtt/unpublish', { method: 'POST' })
      const d = await r.json()
      Alpine.store('app').addToast(d.detail, d.ok ? 'info' : 'error')
    },
  }
}
