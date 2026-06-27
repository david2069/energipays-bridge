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

function mqttSettings() {
  return {
    mqttEnabled: false,
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
        this.mqttConnected = d.connected
        this.mqttHost = d.host
        this.mqttPort = d.port
        this.mqttUsername = d.username
        this.mqttPrefix = d.discovery_prefix
      } catch (_) {}
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
