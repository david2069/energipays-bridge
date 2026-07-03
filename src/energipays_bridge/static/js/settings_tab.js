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
    { key: 'battery_soc',           label: 'Battery SoC %' },
    { key: 'battery_soh',           label: 'Battery SoH %' },
    { key: 'battery_power_w',       label: 'Battery Power W (+charge/−discharge)' },
    { key: 'battery_state',         label: 'Battery State (charging/discharging/idle)' },
    { key: 'battery_grid_status',   label: 'Battery Grid Status' },
    { key: 'battery_inverter_state',label: 'Battery Inverter State' },
    { key: 'battery_capacity_kwh',  label: 'Battery Capacity kWh' },
    { key: 'energy_available_kwh',  label: 'Energy Available kWh' },
    { key: 'home_load_w',           label: 'Home Load W' },
    { key: 'operating_mode',        label: 'Operating Mode (1=Backup 2=Self-Consumption 3=TOU)' },
    { key: 'self_reserve_soc',      label: 'Self Reserve SOC %' },
    { key: 'tou_reserve_soc',       label: 'TOU Reserve SOC %' },
    { key: 'solar_power_w',         label: 'Solar PV Power W' },
  ]

  const _blankMapping = () => ({
    target_metric: '', source: '', scale: 1.0,
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

    addMapping() {
      this.form.mappings.push(_blankMapping())
    },

    _buildMappings() {
      return this.form.mappings
        .filter(m => m.target_metric)
        .map(m => {
          if (this.form.protocol === 'modbus_tcp' || this.form.protocol === 'sunspec_tcp') {
            return { target_metric: m.target_metric, source: `${m._fc}:${m._addr}:${m._rtype}:${m._scale}`, scale: 1 }
          }
          return { target_metric: m.target_metric, source: m.source, scale: m.scale || 1 }
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

    async probeRegister(m, idx) {
      m._probing = true; m._probeResult = null; m._probeScaled = null
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
    mqttPrefix: 'homeassistant',
    republishing: false,
    unpublishing: false,
    actionMsg: '',
    actionOk: true,

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
        this.mqttPrefix = d.discovery_prefix
      } catch (_) {}
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
      if (dev) {
        this.devForm = { id: dev.id, ha_instance_id: dev.ha_instance_id, alias: dev.alias, service_target: dev.service_target, enabled: !!dev.enabled }
      } else {
        this.devForm = { id: null, ha_instance_id: this.instances[0]?.id || '', alias: '', service_target: '', enabled: true }
      }
      this.devFormOpen = true
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
