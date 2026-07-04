function settingsTab() {
  return {
    pollInterval: 60,
    rawAgeDays: 7,
    retentionDays: 30,
    saving: false,

    // Poller status
    pollerStatus: 'unknown',
    pollsTotal: 0,
    lastError: '',

    // DB storage
    metricsEnabled: true,
    dbTables: [],
    dbSizeMb: null,

    // Archive
    archiving: false,
    archiveMsg: '',

    // Backup & export
    backups: [],
    backingUp: false,
    exportHours: '24',

    // Credentials
    changingCredentials: false,
    credEmail: '',
    credPassword: '',
    showCredPw: false,
    credBusy: false,
    credAction: '',
    credMsg: '',
    credOk: false,

    async init() {
      const [pi, ra, rd] = await Promise.all([
        this._get('poll_interval'),
        this._get('raw_age_days'),
        this._get('retention_days'),
      ])
      if (pi) this.pollInterval = parseInt(pi) || 60
      if (ra) this.rawAgeDays = parseInt(ra) || 7
      if (rd) this.retentionDays = parseInt(rd) || 30
      await this._refreshHealth()
      await this._refreshDbStats()
      await this._refreshBackups()
      this._healthTimer = setInterval(() => this._refreshHealth(), 15000)
    },

    destroy() {
      clearInterval(this._healthTimer)
    },

    async _refreshDbStats() {
      try {
        const r = await fetch('/api/admin/db-stats')
        if (!r.ok) return
        const d = await r.json()
        this.dbTables = d.tables || []
        this.dbSizeMb = d.size_mb
        this.metricsEnabled = d.metrics_enabled !== false
      } catch (_) {}
    },

    async toggleMetrics() {
      const next = !this.metricsEnabled
      try {
        const r = await fetch('/api/admin/metrics-enabled', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: next }),
        })
        if (r.ok) {
          this.metricsEnabled = next
          Alpine.store('app').addToast(
            next ? 'Metrics recording enabled' : 'Metrics recording disabled — bridge mode',
            next ? 'info' : 'warn'
          )
        }
      } catch (_) {
        Alpine.store('app').addToast('Failed to update metrics setting', 'error')
      }
    },

    async _refreshBackups() {
      try {
        const r = await fetch('/api/admin/backups')
        if (!r.ok) return
        this.backups = (await r.json()).backups || []
      } catch (_) {}
    },

    async createBackup() {
      this.backingUp = true
      try {
        const r = await fetch('/api/admin/backup', { method: 'POST' })
        const d = await r.json()
        if (r.ok) {
          Alpine.store('app').addToast(`Backup created — ${d.size_kb} KB`)
          await this._refreshBackups()
        } else {
          Alpine.store('app').addToast('Backup failed', 'error')
        }
      } catch (_) {
        Alpine.store('app').addToast('Backup failed', 'error')
      } finally {
        this.backingUp = false
      }
    },

    async _refreshHealth() {
      try {
        const r = await fetch('/api/health')
        if (!r.ok) return
        const d = await r.json()
        const p = d.poller || {}
        this.pollerStatus = p.connected ? 'running' : 'offline'
        this.pollsTotal   = p.polls_total || 0
        this.lastError    = p.last_error  || ''
      } catch (_) {}
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

    async savePollInterval() {
      this.saving = true
      try {
        await this._put('poll_interval', String(this.pollInterval))
        Alpine.store('app').addToast('Poll interval saved — restart to apply')
      } finally { this.saving = false }
    },

    async saveRetentionDays() {
      this.saving = true
      try {
        await this._put('retention_days', String(this.retentionDays))
        Alpine.store('app').addToast('Retention saved')
      } finally { this.saving = false }
    },

    async saveRawDays() {
      this.saving = true
      try {
        await this._put('raw_age_days', String(this.rawAgeDays))
        Alpine.store('app').addToast('Raw retention saved')
      } finally { this.saving = false }
    },

    async archiveNow() {
      this.archiving = true
      this.archiveMsg = ''
      try {
        const r = await fetch('/api/admin/archive-now', { method: 'POST' })
        if (r.ok) {
          this.archiveMsg = 'Done'
          setTimeout(() => { this.archiveMsg = '' }, 3000)
          Alpine.store('app').addToast('Archive completed')
          await this._refreshDbStats()
        } else {
          Alpine.store('app').addToast('Archive failed', 'error')
        }
      } catch (_) {
        Alpine.store('app').addToast('Archive failed', 'error')
      } finally {
        this.archiving = false
      }
    },

    async testCredentials() {
      this.credBusy = true
      this.credAction = 'test'
      this.credMsg = ''
      try {
        const r = await fetch('/api/setup/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: this.credEmail, password: this.credPassword }),
        })
        const d = await r.json()
        if (d.ok) {
          this.credOk = true
          this.credMsg = `Connected as ${d.user?.name || d.user?.email || this.credEmail}`
        } else {
          this.credOk = false
          this.credMsg = d.error || 'Connection failed'
        }
      } catch (e) {
        this.credOk = false
        this.credMsg = 'Network error'
      } finally {
        this.credBusy = false
      }
    },

    async saveCredentials() {
      this.credBusy = true
      this.credAction = 'save'
      this.credMsg = ''
      try {
        const r = await fetch('/api/setup/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: this.credEmail, password: this.credPassword }),
        })
        const d = await r.json()
        if (r.ok && d.ok) {
          this.credOk = true
          this.credMsg = `Saved — reconnecting as ${d.user?.name || this.credEmail}`
          Alpine.store('app').addToast('Account updated — poller reconnecting', 'info')
          setTimeout(() => {
            this.changingCredentials = false
            this.credMsg = ''
            this.credPassword = ''
          }, 2000)
        } else {
          this.credOk = false
          this.credMsg = d.error || 'Save failed'
        }
      } catch (e) {
        this.credOk = false
        this.credMsg = 'Network error'
      } finally {
        this.credBusy = false
      }
    },
  }
}

function integrationsCard() {
  const METRIC_KEYS = [
    // Battery
    { key: 'battery_soc',           label: 'Battery SoC %' },
    { key: 'battery_soh',           label: 'Battery SoH %' },
    { key: 'battery_power_w',       label: 'Battery Power W (+charge/−discharge)' },
    { key: 'battery_state',         label: 'Battery State (charging/discharging/idle)' },
    { key: 'battery_capacity_kwh',  label: 'Battery Capacity kWh' },
    { key: 'energy_available_kwh',  label: 'Energy Available kWh' },
    // Solar / Inverter
    { key: 'solar_power_w',         label: 'Solar PV Power W' },
    { key: 'grid_status',           label: 'Grid Status (701.DERMode)' },
    { key: 'inverter_state',        label: 'Inverter State (701.InvSt)' },
    { key: 'connection_state',      label: 'Connection State (701.ConnSt)' },
    { key: 'ambient_temp_c',        label: 'Ambient Temperature °C (701.TmpAmb)' },
    { key: 'cabinet_temp_c',        label: 'Cabinet Temperature °C (701.TmpCab)' },
    // System
    { key: 'home_load_w',           label: 'Home Load W' },
    { key: 'operating_mode',        label: 'Operating Mode (1=Backup 2=Self 3=TOU)' },
    { key: 'self_reserve_soc',      label: 'Self Reserve SOC %' },
    { key: 'tou_reserve_soc',       label: 'TOU Reserve SOC %' },
    { key: 'battery_grid_status',   label: 'Battery Grid Status' },
    { key: 'battery_inverter_state',label: 'Battery Inverter State' },
  ]

  const _blankMapping = () => ({
    target_metric: '', source: '', scale: 1.0, enabled: true,
    _fc: 'fc3', _addr: '', _rtype: 'uint16', _scale: 1.0,
    _probing: false, _probeResult: null, _probeScaled: null,
  })

  const _blankForm = () => ({
    type: 'battery', protocol: 'rest', name: '',
    config: { base_url: '', endpoint: '/api/points/latest', auth_type: 'none', auth_token: '', poll_interval: 30,
               host: '', port: 502, unit_id: 1, url: '', token: '', username: '', password: '' },
    mappings: [_blankMapping()],
    enabled: true,
  })

  return {
    integrations: [],
    loading: false,
    wizardOpen: false,
    editingId: null,
    form: _blankForm(),
    saving: false,
    testResult: null,
    metricKeys: METRIC_KEYS,

    // SunSpec discovery state
    ssDiscovering: false,
    ssModels: [],
    ssBaseAddress: null,
    ssError: null,

    // Entity browser state
    entityBrowserOpen: false,
    ebMappingIdx: null,
    ebSearch: '',
    ebDomain: '',
    ebPage: 1,
    ebPages: 1,
    ebTotal: 0,
    ebEntities: [],
    ebDomains: [],
    ebLoading: false,

    async init() {
      await this.load()
    },

    async load() {
      this.loading = true
      try {
        const r = await fetch('/api/integrations')
        if (r.ok) this.integrations = await r.json()
      } catch (_) {}
      this.loading = false
    },

    openAdd() {
      this.editingId = null
      this.form = _blankForm()
      this.testResult = null
      this.wizardOpen = true
    },

    openEdit(intg) {
      this.editingId = intg.id
      this.form = {
        type: intg.type,
        protocol: intg.protocol,
        name: intg.name,
        config: { base_url: '', endpoint: '/api/points/latest', auth_type: 'none', auth_token: '',
                  poll_interval: 30, host: '', port: 502, unit_id: 1, url: '', token: '',
                  username: '', password: '', ...(intg.config || {}) },
        mappings: (intg.mappings || []).map(m => ({
          ...m,
          _fc: m.source?.split(':')[0] || 'fc3',
          _addr: parseInt(m.source?.split(':')[1]) || '',
          _rtype: m.source?.split(':')[2] || 'uint16',
          _scale: parseFloat(m.source?.split(':')[3]) || 1.0,
          _probing: false, _probeResult: null, _probeScaled: null,
        })),
        enabled: intg.enabled,
      }
      if (!this.form.mappings.length) this.form.mappings.push(_blankMapping())
      this.testResult = null
      this.wizardOpen = true
    },

    onProtocolChange(newProto) {
      if (newProto === this.form.protocol) return
      this.form.protocol = newProto
      this.form.mappings = [_blankMapping()]
      this.testResult = null
      this.ssModels = []
      this.ssError = null
    },

    async discoverSunSpec() {
      this.ssDiscovering = true
      this.ssModels = []
      this.ssBaseAddress = null
      this.ssError = null
      try {
        const r = await fetch('/api/integrations/sunspec-discover', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            host: this.form.config.host,
            port: this.form.config.port || 502,
            unit_id: this.form.config.unit_id || 1,
            sunspec_start: this.form.config.sunspec_start ?? 0,
          }),
        })
        const d = await r.json()
        if (r.ok) {
          this.ssModels = d.models || []
          this.ssBaseAddress = d.base_address
          if (this.ssModels.length === 0) this.ssError = 'No SunSpec models found on this device.'
        } else {
          this.ssError = d.detail || 'Discovery failed'
        }
      } catch (e) {
        this.ssError = String(e)
      }
      this.ssDiscovering = false
    },

    applyDiscoveredMappings(model) {
      const newMappings = model.suggested_mappings.map(s => ({
        target_metric: s.target_metric,
        source: s.source,
        scale: 1.0,
        // parse back for display fields
        _fc: s.source.split(':')[0] || 'fc3',
        _addr: parseInt(s.source.split(':')[1]) || '',
        _rtype: s.source.split(':')[2] || 'uint16',
        _scale: parseFloat(s.source.split(':')[3]) || 1.0,
        _probing: false,
        _probeResult: s.live_value != null ? s.live_value : null,
        _probeScaled: s.live_value != null ? s.live_value : null,
      }))
      // Merge: add new ones that don't already have the same target_metric
      const existing = new Set(this.form.mappings.map(m => m.target_metric).filter(Boolean))
      for (const m of newMappings) {
        if (!existing.has(m.target_metric)) {
          this.form.mappings.push(m)
          existing.add(m.target_metric)
        }
      }
      // Remove blank placeholder if it was the only entry
      this.form.mappings = this.form.mappings.filter(m => m.target_metric)
      Alpine.store('app').addToast(`${newMappings.length} mapping(s) applied from Model ${model.model_id}`)
    },

    applyAllDiscoveredMappings() {
      let total = 0
      for (const model of this.ssModels) {
        if (!model.suggested_mappings?.length) continue
        const existing = new Set(this.form.mappings.map(m => m.target_metric).filter(Boolean))
        for (const s of model.suggested_mappings) {
          if (existing.has(s.target_metric)) continue
          this.form.mappings.push({
            target_metric: s.target_metric,
            source: s.source,
            scale: 1.0,
            _fc: s.source.split(':')[0] || 'fc3',
            _addr: parseInt(s.source.split(':')[1]) || '',
            _rtype: s.source.split(':')[2] || 'uint16',
            _scale: parseFloat(s.source.split(':')[3]) || 1.0,
            _probing: false,
            _probeResult: s.live_value != null ? s.live_value : null,
            _probeScaled: s.live_value != null ? s.live_value : null,
          })
          existing.add(s.target_metric)
          total++
        }
      }
      this.form.mappings = this.form.mappings.filter(m => m.target_metric)
      Alpine.store('app').addToast(`${total} mapping(s) applied from all models`)
    },

    addMapping() {
      this.form.mappings.push(_blankMapping())
    },

    _buildMappings() {
      return this.form.mappings
        .filter(m => m.target_metric)
        .map(m => {
          const enabled = m.enabled !== false  // default true
          if (this.form.protocol === 'modbus_tcp' || this.form.protocol === 'sunspec_tcp') {
            return { target_metric: m.target_metric, source: `${m._fc}:${m._addr}:${m._rtype}:${m._scale}`, scale: 1, enabled }
          }
          return { target_metric: m.target_metric, source: m.source, scale: m.scale || 1, enabled }
        })
    },

    async saveIntegration() {
      this.saving = true
      this.testResult = null
      const payload = {
        type: this.form.type, protocol: this.form.protocol, name: this.form.name,
        config: this.form.config, mappings: this._buildMappings(), enabled: this.form.enabled,
      }
      try {
        const url = this.editingId ? `/api/integrations/${this.editingId}` : '/api/integrations'
        const method = this.editingId ? 'PUT' : 'POST'
        const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        if (r.ok) {
          await this.load()
          this.wizardOpen = false
          Alpine.store('app').addToast(this.editingId ? 'Integration updated' : 'Integration added')
        } else {
          const d = await r.json()
          Alpine.store('app').addToast(d.detail || 'Save failed', 'error')
        }
      } catch (_) {
        Alpine.store('app').addToast('Network error', 'error')
      }
      this.saving = false
    },

    async testIntegration() {
      if (!this.editingId) return
      this.testResult = null
      const r = await fetch(`/api/integrations/${this.editingId}/test`, { method: 'POST' })
      this.testResult = await r.json()
    },

    async toggleEnabled(intg) {
      const r = await fetch(`/api/integrations/${intg.id}/enable`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !intg.enabled }),
      })
      if (r.ok) await this.load()
    },

    async deleteIntg(intg) {
      if (!confirm(`Delete integration "${intg.name}"?`)) return
      const r = await fetch(`/api/integrations/${intg.id}`, { method: 'DELETE' })
      if (r.ok) {
        await this.load()
        Alpine.store('app').addToast('Integration deleted')
      }
    },

    _decodeLabel(metric, value) {
      if (value == null) return null
      const v = parseInt(value)
      const INV_ST = {0:'Off',1:'Sleeping',2:'Starting',3:'Running',4:'Throttled',5:'Shutting Down',6:'Fault',7:'Standby'}
      if (metric === 'inverter_state') return INV_ST[v] ?? null
      if (metric === 'connection_state') return v === 1 ? 'Connected' : 'Disconnected'
      if (metric === 'grid_status') {
        if (v & 2) return 'Grid Forming'
        if (v & 1) return 'Grid Following'
        if (v & 4) return 'PV Clipped'
        return `Mode ${v}`
      }
      return null
    },

    async probeRegister(m, idx) {
      m._probing = true; m._probeResult = null; m._probeScaled = null; m._probeLabel = null
      try {
        const r = await fetch('/api/integrations/probe', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            host: this.form.config.host, port: this.form.config.port || 502,
            unit_id: this.form.config.unit_id || 1,
            fc: parseInt(m._fc.replace('fc', '')),
            address: parseInt(m._addr) - (this.form.config.base_address_offset || 0),
            count: 1, type: m._rtype,
          }),
        })
        const d = await r.json()
        if (d.ok && d.registers?.length) {
          m._probeResult = d.registers[0]
          m._probeScaled = +(d.registers[0] * (m._scale || 1)).toFixed(4)
          m._probeLabel = this._decodeLabel(m.target_metric, m._probeScaled)
        } else {
          m._probeResult = d.error || 'error'
        }
      } catch (e) {
        m._probeResult = String(e)
      }
      m._probing = false
    },

    openEntityBrowser(idx) {
      this.ebMappingIdx = idx
      this.ebSearch = ''
      this.ebDomain = ''
      this.ebPage = 1
      this.ebEntities = []
      this.ebDomains = []
      this.entityBrowserOpen = true
      this.ebLoad()
    },

    async ebLoad() {
      this.ebLoading = true
      try {
        const r = await fetch('/api/integrations/ha-entities', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: this.form.config.url, token: this.form.config.token,
            domain: this.ebDomain, search: this.ebSearch,
            page: this.ebPage, page_size: 50,
          }),
        })
        if (r.ok) {
          const d = await r.json()
          this.ebEntities = d.entities || []
          this.ebTotal = d.total || 0
          this.ebPages = d.pages || 1
          this.ebDomains = d.domains || []
        }
      } catch (_) {}
      this.ebLoading = false
    },

    selectEntity(e) {
      const m = this.form.mappings[this.ebMappingIdx]
      if (m) m.source = e.entity_id
      this.entityBrowserOpen = false
    },
  }
}

function mqttSettings() {
  return {
    mqttEnabled: false,
    mqttPaused: false,
    mqttConfirmOff: false,
    mqttConnected: false,
    mqttHost: '—',
    mqttPort: '—',
    mqttUsername: '',
    mqttTls: false,
    mqttPrefix: 'homeassistant',
    republishing: false,
    unpublishing: false,
    actionMsg: '',
    actionOk: true,

    // ── Edit form ────────────────────────────────────────────────────────────
    editing: false,
    editHost: '',
    editPort: '1883',
    editUsername: '',
    editPassword: '',
    editTls: false,
    discovering: false,
    discoverMsg: '',
    discoverOk: false,
    testing: false,
    testMsg: '',
    testOk: false,
    saving: false,

    async init() {
      try {
        const r = await fetch('/api/mqtt/config')
        if (!r.ok) return
        const d = await r.json()
        this.mqttEnabled = d.enabled
        this.mqttPaused = d.paused
        this.mqttConnected = d.connected
        this.mqttHost = d.host
        this.mqttPort = d.port
        this.mqttUsername = d.username
        this.mqttTls = !!d.tls
        this.mqttPrefix = d.discovery_prefix
      } catch (_) {}
    },

    openEdit() {
      this.editHost = this.mqttEnabled ? this.mqttHost : ''
      this.editPort = String(this.mqttEnabled ? this.mqttPort : 1883)
      this.editUsername = this.mqttEnabled ? this.mqttUsername : ''
      this.editPassword = ''
      this.editTls = this.mqttTls
      this.discoverMsg = ''; this.testMsg = ''
      this.editing = true
      if (!this.editHost) this.runDiscover()
    },

    async runDiscover() {
      this.discovering = true; this.discoverMsg = ''
      try {
        const r = await fetch('api/mqtt/discover')
        const d = await r.json()
        if (d.found) {
          this.editHost = d.host
          this.editPort = String(d.port)
          if (d.username) this.editUsername = d.username
          if (d.password) this.editPassword = d.password
          this.editTls = !!d.tls
          this.discoverOk = true
          this.discoverMsg = d.source === 'supervisor'
            ? 'Found HA broker via Supervisor (credentials included)'
            : `Found broker at ${d.host}:${d.port}`
        } else {
          this.discoverOk = false
          this.discoverMsg = d.error || 'No broker found — enter details manually'
        }
      } catch (e) {
        this.discoverOk = false
        this.discoverMsg = 'Discovery request failed'
      } finally { this.discovering = false }
    },

    async testMqtt() {
      this.testing = true; this.testMsg = ''
      try {
        const r = await fetch('api/mqtt/test', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            host: this.editHost, port: parseInt(this.editPort) || 1883,
            username: this.editUsername, password: this.editPassword, tls: this.editTls,
          }),
        })
        const d = await r.json()
        this.testOk = !!d.ok
        this.testMsg = d.ok ? 'Connected successfully' : (d.error || 'Connection failed')
      } catch (e) {
        this.testOk = false
        this.testMsg = 'Network error'
      } finally { this.testing = false }
    },

    async saveEdit() {
      this.saving = true
      try {
        const r = await fetch('api/mqtt/config', {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            enabled: true, host: this.editHost, port: parseInt(this.editPort) || 1883,
            username: this.editUsername, password: this.editPassword || null, tls: this.editTls,
          }),
        })
        const d = await r.json()
        if (d.ok) {
          this.editing = false
          Alpine.store('app').addToast('MQTT settings saved', 'success')
          await this.init()
        } else {
          Alpine.store('app').addToast('Failed to save MQTT settings', 'error')
        }
      } catch (e) {
        Alpine.store('app').addToast('Network error saving MQTT settings', 'error')
      } finally { this.saving = false }
    },

    async toggleMqttPause() {
      try {
        const r = await fetch('/api/mqtt/toggle-pause', { method: 'POST' })
        const d = await r.json()
        if (d.ok) {
          this.mqttPaused = d.paused
          Alpine.store('app').addToast(d.paused ? 'MQTT publishing paused' : 'MQTT publishing resumed')
        }
      } catch (_) {
        Alpine.store('app').addToast('MQTT toggle failed', 'error')
      }
    },

    async republish() {
      this.republishing = true
      this.actionMsg = ''
      try {
        const r = await fetch('/api/mqtt/republish', { method: 'POST' })
        const d = await r.json()
        this.actionOk = d.ok
        this.actionMsg = d.detail
      } catch (_) {
        this.actionOk = false
        this.actionMsg = 'Network error'
      } finally {
        this.republishing = false
        setTimeout(() => this.actionMsg = '', 4000)
      }
    },

    async unpublish() {
      if (!confirm('Remove all Energipays entities from Home Assistant?')) return
      this.unpublishing = true
      this.actionMsg = ''
      try {
        const r = await fetch('/api/mqtt/unpublish', { method: 'POST' })
        const d = await r.json()
        this.actionOk = d.ok
        this.actionMsg = d.detail
      } catch (_) {
        this.actionOk = false
        this.actionMsg = 'Network error'
      } finally {
        this.unpublishing = false
        setTimeout(() => this.actionMsg = '', 4000)
      }
    },
  }
}

function notificationsCard() {
  return {
    // ── HA Instances ──────────────────────────────────────────────────────
    instances: [],
    instLoading: false,
    instFormOpen: false,
    instTesting: false,
    instSaving: false,
    instFormError: '',
    instTestResult: '',
    instTestOk: false,
    instForm: { id: null, alias: '', host: '', token: '', is_default: false },

    // ── Companion Devices ─────────────────────────────────────────────────
    devices: [],
    devLoading: false,
    devFormOpen: false,
    devSaving: false,
    devDiscovering: false,
    devTesting: false,
    devTestResult: '',
    devTestOk: false,
    devFormError: '',
    devTargets: [],
    devForm: { id: null, ha_instance_id: '', alias: '', service_target: '', enabled: true },

    // ── Notification Settings ─────────────────────────────────────────────
    notifSettings: { enabled: false, triggers: [], temp_threshold: 40 },
    testSending: false,
    testResult: '',
    testOk: false,
    notifStats: {},       // {event_type: {count, last_ts}}
    expandedLogKey: null, // which trigger row is expanded
    logEntries: {},       // {event_type: [...log rows]}

    triggerDefs: [
      ['device_online',   'Device online status',   'Enable/disable device online push notifications'],
      ['device_offline',  'Device offline status',  'Enable/disable device offline push notifications'],
      ['offpeak_started', 'Off-Peak status',        'Enable/disable Off-Peak rule start notifications'],
      ['offpeak_ended',   'Off-Peak ended',         'Enable/disable Off-Peak rule stop notifications'],
      ['boost_started',   'Boost status',           'Enable/disable boost start push notifications'],
      ['boost_ended',     'Boost ended',            'Enable/disable boost completion notifications'],
      ['done_heating',    'Done status',            'Enable/disable heating-done push notifications'],
      ['temp_threshold',  'Device temperature threshold status', 'Enable/disable temperature threshold notifications (works if T-data is available)'],
    ],

    async init() {
      await Promise.all([this.loadInstances(), this.loadDevices(), this.loadSettings(), this.loadStats()])
    },

    // ── Instances ─────────────────────────────────────────────────────────
    async loadInstances() {
      this.instLoading = true
      try {
        const r = await fetch('/api/ha/instances')
        if (r.ok) this.instances = await r.json()
      } catch (_) {}
      finally { this.instLoading = false }
    },

    openInstForm(inst) {
      this.instFormError = ''
      this.instTestResult = ''
      this.instTestOk = inst != null  // existing instances are assumed already tested
      if (inst) {
        this.instForm = { ...inst, token: '••••' }
      } else {
        this.instForm = { id: null, alias: '', host: '', token: '', is_default: false }
      }
      this.instFormOpen = true
    },

    async testInstance() {
      this.instTesting = true
      this.instTestResult = ''
      this.instFormError = ''
      try {
        const body = { ...this.instForm, id: this.instForm.id || crypto.randomUUID(), enabled: true }
        const r = await fetch('/api/ha/instances', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        const d = await r.json()
        if (r.ok) {
          this.instTestOk = true
          this.instTestResult = '✓ Reachable — HA responded successfully'
          // Store the id that the test used so Save can reuse it
          if (!this.instForm.id) this.instForm._testId = body.id
        } else {
          this.instTestOk = false
          this.instTestResult = '✗ ' + (d.detail || 'Connection failed')
        }
      } catch (e) {
        this.instTestOk = false
        this.instTestResult = '✗ ' + String(e)
      }
      this.instTesting = false
    },

    saveInstanceWithWarning() {
      if (confirm('You have not tested this connection. Save anyway?')) {
        this.saveInstance()
      }
    },

    async saveInstance() {
      if (!this.instForm.alias || !this.instForm.host || !this.instForm.token) {
        this.instFormError = 'Alias, URL and token are required'
        return
      }
      this.instSaving = true
      this.instFormError = ''
      try {
        const body = { ...this.instForm, id: this.instForm.id || this.instForm._testId || crypto.randomUUID(), enabled: true }
        delete body._testId
        const r = await fetch('/api/ha/instances', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        if (r.ok) {
          this.instFormOpen = false
          await this.loadInstances()
        } else {
          const d = await r.json()
          this.instFormError = d.detail || 'Save failed'
        }
      } catch (e) { this.instFormError = String(e) }
      this.instSaving = false
    },

    async deleteInstance(id) {
      if (!confirm('Delete this HA instance? All companion devices linked to it will also be removed.')) return
      await fetch(`/api/ha/instances/${id}`, { method: 'DELETE' })
      await Promise.all([this.loadInstances(), this.loadDevices()])
    },

    // Supervisor-sourced instance: identity (alias/host/token) is auto-managed
    // and re-synced every boot — only the enabled flag is user-editable, via
    // this same upsert endpoint (the backend ignores everything else we send
    // here for a source==='supervisor' row).
    async toggleInstanceEnabled(inst) {
      try {
        const r = await fetch('/api/ha/instances', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            id: inst.id, alias: inst.alias, host: inst.host, token: '••••',
            enabled: !inst.enabled, is_default: inst.is_default,
          }),
        })
        if (r.ok) {
          inst.enabled = !inst.enabled
          Alpine.store('app').addToast(inst.enabled ? 'Instance enabled' : 'Instance disabled', 'success')
        } else {
          Alpine.store('app').addToast('Failed to update instance', 'error')
        }
      } catch (e) {
        Alpine.store('app').addToast('Network error', 'error')
      }
    },

    // ── Devices ───────────────────────────────────────────────────────────
    async loadDevices() {
      this.devLoading = true
      try {
        const r = await fetch('/api/ha/devices')
        if (r.ok) this.devices = await r.json()
      } catch (_) {}
      finally { this.devLoading = false }
    },

    openDevForm(dev) {
      this.devFormError = ''
      this.devTargets = []
      this.devTestResult = ''
      this.devTestOk = false
      if (dev) {
        this.devForm = { id: dev.id, ha_instance_id: dev.ha_instance_id, alias: dev.alias, service_target: dev.service_target, enabled: !!dev.enabled }
      } else {
        this.devForm = { id: null, ha_instance_id: this.instances[0]?.id || '', alias: '', service_target: '', enabled: true }
      }
      this.devFormOpen = true
    },

    async testDeviceNotification() {
      if (!this.devForm.service_target || !this.devForm.ha_instance_id) return
      this.devTesting = true
      this.devTestResult = ''
      this.devFormError = ''
      try {
        // Save temporarily if new (needed for test via /api/notifications/test which sends to all enabled)
        // Instead, test directly via HA service using the instance token
        const inst = this.instances.find(i => i.id === this.devForm.ha_instance_id)
        if (!inst) { this.devTestResult = '✗ HA instance not found'; this.devTestOk = false; this.devTesting = false; return }
        // Save device temporarily then trigger test, then we can reload
        const tempId = this.devForm.id || ('tmp-' + crypto.randomUUID())
        const body = { ...this.devForm, id: tempId, enabled: true }
        const saveR = await fetch('/api/ha/devices', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        if (!saveR.ok) { this.devTestResult = '✗ Could not register device for test'; this.devTestOk = false; this.devTesting = false; return }
        if (!this.devForm.id) this.devForm.id = tempId
        const r = await fetch('/api/notifications/test', { method: 'POST' })
        const d = await r.json()
        this.devTestOk = d.sent
        this.devTestResult = d.sent
          ? `✓ Notification sent to ${this.devForm.alias || this.devForm.service_target}`
          : ('✗ ' + (d.reason === 'disabled' ? 'Enable notifications first' : d.reason === 'no_devices' ? 'No devices found' : (d.reason || 'Send failed')))
        await this.loadDevices()
      } catch (e) {
        this.devTestOk = false
        this.devTestResult = '✗ ' + String(e)
      }
      this.devTesting = false
    },

    async discoverTargets() {
      if (!this.devForm.ha_instance_id) return
      this.devDiscovering = true
      this.devTargets = []
      try {
        const r = await fetch(`/api/ha/instances/${this.devForm.ha_instance_id}/targets`)
        if (r.ok) this.devTargets = await r.json()
        else { const d = await r.json(); this.devFormError = d.detail || 'Discovery failed' }
      } catch (e) { this.devFormError = String(e) }
      this.devDiscovering = false
    },

    async saveDevice() {
      if (!this.devForm.ha_instance_id || !this.devForm.alias || !this.devForm.service_target) {
        this.devFormError = 'All fields are required'
        return
      }
      this.devSaving = true
      this.devFormError = ''
      try {
        const body = { ...this.devForm, id: this.devForm.id || crypto.randomUUID() }
        const r = await fetch('/api/ha/devices', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        if (r.ok) {
          this.devFormOpen = false
          await this.loadDevices()
        } else {
          const d = await r.json()
          this.devFormError = d.detail || 'Save failed'
        }
      } catch (e) { this.devFormError = String(e) }
      this.devSaving = false
    },

    async deleteDevice(id) {
      if (!confirm('Remove this companion device?')) return
      await fetch(`/api/ha/devices/${id}`, { method: 'DELETE' })
      await this.loadDevices()
    },

    // ── Notification Settings ─────────────────────────────────────────────
    async loadSettings() {
      try {
        const r = await fetch('/api/notification-settings')
        if (r.ok) this.notifSettings = await r.json()
      } catch (_) {}
    },

    async saveSettings() {
      try {
        await fetch('/api/notification-settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.notifSettings),
        })
      } catch (_) {}
    },

    setMaster(val) {
      this.notifSettings.enabled = val
      this.saveSettings()
    },

    hasTrigger(key) {
      return (this.notifSettings.triggers || []).includes(key)
    },

    setTrigger(key, val) {
      const triggers = [...(this.notifSettings.triggers || [])]
      if (val && !triggers.includes(key)) triggers.push(key)
      if (!val) { const i = triggers.indexOf(key); if (i >= 0) triggers.splice(i, 1) }
      this.notifSettings.triggers = triggers
      this.saveSettings()
    },

    async loadStats() {
      try {
        const r = await fetch('/api/notifications/stats')
        if (r.ok) this.notifStats = await r.json()
      } catch (_) {}
    },

    async toggleLogRow(key) {
      if (this.expandedLogKey === key) {
        this.expandedLogKey = null
        return
      }
      this.expandedLogKey = key
      if (!this.logEntries[key]) {
        try {
          const r = await fetch(`/api/notifications/log?event_type=${key}&limit=10`)
          if (r.ok) this.logEntries[key] = await r.json()
        } catch (_) { this.logEntries[key] = [] }
      }
    },

    fmtTs(ts) {
      if (!ts) return '—'
      const d = new Date(ts * 1000)
      const now = new Date()
      const sameDay = d.toDateString() === now.toDateString()
      if (sameDay) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    },

    // ── Test ──────────────────────────────────────────────────────────────
    async sendTest() {
      this.testSending = true
      this.testResult = ''
      try {
        const r = await fetch('/api/notifications/test', { method: 'POST' })
        const d = await r.json()
        this.testOk = d.sent
        if (d.sent) {
          this.testResult = `✓ Sent to ${d.results?.length || 1} device(s)`
        } else {
          const reason = { disabled: 'Notifications are disabled', no_devices: 'No companion devices configured', all_failed: 'All devices failed — check HA token and URL' }
          this.testResult = '✗ ' + (reason[d.reason] || d.reason || 'Failed')
        }
      } catch (e) {
        this.testOk = false
        this.testResult = '✗ ' + String(e)
      }
      this.testSending = false
    },
  }
}

function httpStatsCard() {
  return {
    loading: false,
    available: false,
    started_ts: null,
    total_requests: 0,
    last_request_ts: null,
    error_count: 0,
    last_error_ts: null,
    last_error: '',

    async load() {
      this.loading = true
      try {
        const r = await fetch('/api/http-stats')
        if (r.ok) {
          const d = await r.json()
          this.available   = d.available
          this.started_ts  = d.started_ts   ?? null
          this.total_requests = d.total_requests ?? 0
          this.last_request_ts = d.last_request_ts ?? null
          this.error_count = d.error_count  ?? 0
          this.last_error_ts = d.last_error_ts ?? null
          this.last_error  = d.last_error   ?? ''
        }
      } catch (_) {}
      finally { this.loading = false }
    },

    fmtTs(ts) {
      if (!ts) return '—'
      return new Date(ts * 1000).toLocaleString()
    },
  }
}
