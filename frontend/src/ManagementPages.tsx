import { useEffect, useState } from 'react'
import { Activity, ArrowRight, Check, RefreshCw, ShieldCheck } from 'lucide-react'
import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export type AuthInfo = { setup_required: boolean; authenticated: boolean; username: string | null; role?: string | null; permissions?: string[]; theme?: string; must_change_password?: boolean; default_login_available?: boolean; setup_instructions: string | null }
type Entry = Record<string, string | number | boolean | string[] | null | undefined>
type HistoryDiscordConfig = { enabled: boolean; configured: boolean; webhook_url_masked: string; webhook_username: string; avatar_url: string }
type PackageUpdate = { current_version: string; latest_version: string; update_available: boolean; update_command: string; package_url: string }
type Kind = 'services' | 'jobs' | 'network' | 'storage' | 'history' | 'audit' | 'security' | 'logs' | 'settings'

async function api(path: string, init?: RequestInit) {
  const response = await fetch(path, init)
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail ?? `Request failed (HTTP ${response.status})`)
  return body
}

export function AuthPage({ auth, onSuccess }: { auth: AuthInfo; onSuccess: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [setupToken, setSetupToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const setup = auth.setup_required
  const forcePasswordChange = Boolean(auth.authenticated && auth.must_change_password)
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      if (forcePasswordChange) {
        if (password !== confirmation) throw new Error('The new passwords do not match.')
        await api('/api/v1/auth/change-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ new_password: password }) })
      } else {
        await api(setup ? '/api/v1/auth/setup' : '/api/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(setup ? { setup_token: setupToken, username, password } : { username, password }) })
      }
      onSuccess()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Sign in failed') } finally { setBusy(false) }
  }
  return <main className="auth-screen"><form className="panel auth-card" onSubmit={submit}><div className="brand-icon"><ShieldCheck size={21}/></div><h1>{forcePasswordChange ? 'Set your new password' : setup ? 'Secure your Synfull dashboard' : 'Welcome back'}</h1><p>{forcePasswordChange ? 'The default local admin password must be changed before you can continue.' : setup ? 'Create the first administrator account. The one-time setup token is stored in the Synfull state directory.' : 'Sign in to access host monitoring and management.'}</p>{setup && <><label className="form-label" htmlFor="setup-token">One-time setup token</label><input id="setup-token" className="form-input" type="password" autoComplete="off" value={setupToken} onChange={event => setSetupToken(event.target.value)} required/><small>{auth.setup_instructions}</small></>}{!forcePasswordChange && <><label className="form-label" htmlFor="admin-user">Username</label><input id="admin-user" className="form-input" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required/><label className="form-label" htmlFor="admin-password">Password</label></>}<input id="admin-password" aria-label={forcePasswordChange ? 'New password' : 'Password'} className="form-input" type="password" autoComplete={setup || forcePasswordChange ? 'new-password' : 'current-password'} minLength={setup || forcePasswordChange ? 12 : undefined} placeholder={forcePasswordChange ? 'New password (12+ characters)' : ''} value={password} onChange={event => setPassword(event.target.value)} required/>{forcePasswordChange && <><label className="form-label" htmlFor="confirm-password">Confirm new password</label><input id="confirm-password" className="form-input" type="password" autoComplete="new-password" minLength={12} value={confirmation} onChange={event => setConfirmation(event.target.value)} required/><small>Choose at least 12 characters. You must change this password before accessing the dashboard.</small></>}{setup && <small>Use at least 12 characters. Your password is stored as a salted scrypt hash.</small>}{!setup && !forcePasswordChange && auth.default_login_available && <small>First local sign-in: username <strong>admin</strong>, password <strong>admin</strong>. You will be required to change it before continuing.</small>}{error && <div className="alert" role="alert">{error}</div>}<button className="console-button auth-submit" disabled={busy||Boolean(forcePasswordChange&&(password.length<12||password!==confirmation))}>{busy ? 'Please wait…' : forcePasswordChange ? 'Change password' : setup ? 'Create administrator' : 'Sign in'} <ArrowRight size={14}/></button></form></main>
}

const titles: Record<Kind, [string, string, string]> = {
  services: ['Services', 'Service status and allowlisted controls.', '/api/v1/management/services'],
  jobs: ['Scheduled jobs', 'Windows Task Scheduler, systemd timers, and available cron entries.', '/api/v1/management/jobs'],
  network: ['Network', 'Configured endpoint availability and TLS certificate expiry.', '/api/v1/management/endpoints'],
  storage: ['Storage & backups', 'Volume capacity, SMART health, and configured backup freshness checks.', '/api/v1/management/backups'],
  history: ['History & alerts', 'Persistent CPU, memory, and disk history with threshold notifications.', '/api/v1/management/metrics/history?hours=24'],
  audit: ['Audit log', 'Recent administrator sign-ins and management actions.', '/api/v1/management/audit'],
  security: ['Security', 'Session status, remote access posture, and service action allowlist.', '/api/v1/management/security'],
  logs: ['System logs', 'Search recent Windows System or Linux journal entries.', '/api/v1/management/logs'],
  settings: ['Settings', 'Choose your dashboard theme and manage user accounts.', '/api/v1/management/users'],
}

export function ManagementPage({ kind, auth, onThemeChange }: { kind: Kind; auth?: AuthInfo; onThemeChange?: (theme: string) => void }) {
  const [rows, setRows] = useState<Entry[]>([])
  const [secondary, setSecondary] = useState<Entry[]>([])
  const [volumes, setVolumes] = useState<Entry[]>([])
  const [thresholds, setThresholds] = useState<Record<string, number>>({ cpu_percent: 90, memory_percent: 90, disk_percent: 90 })
  const [allowlist, setAllowlist] = useState<string[]>([])
  const [savedTargets, setSavedTargets] = useState<Entry[]>([])
  const [query, setQuery] = useState('')
  const [name, setName] = useState('')
  const [target, setTarget] = useState('')
  const [freshness, setFreshness] = useState(48)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [userRows, setUserRows] = useState<Entry[]>([])
  const [availablePermissions, setAvailablePermissions] = useState<Record<string, string>>({})
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newRole, setNewRole] = useState('guest')
  const [newPermissions, setNewPermissions] = useState<string[]>([])
  const [newTheme, setNewTheme] = useState('synfull')
  const [passwordResets, setPasswordResets] = useState<Record<string, string>>({})
  const [settingsRole, setSettingsRole] = useState(auth?.role)
  const [historyDiscord, setHistoryDiscord] = useState<HistoryDiscordConfig | null>(null)
  const [historyWebhookInput, setHistoryWebhookInput] = useState('')
  const [historyDiscordBusy, setHistoryDiscordBusy] = useState(false)
  const [historyDiscordMessage, setHistoryDiscordMessage] = useState('')
  const [historyDiscordError, setHistoryDiscordError] = useState('')
  const [installedVersion, setInstalledVersion] = useState('Loading…')
  const [packageUpdate, setPackageUpdate] = useState<PackageUpdate>({
    current_version: 'unknown', latest_version: '', update_available: false,
    update_command: 'npm install -g synfull@latest\nsynfull reload',
    package_url: 'https://www.npmjs.com/package/synfull',
  })
  const [updateCheckBusy, setUpdateCheckBusy] = useState(false)
  const [updateCheckError, setUpdateCheckError] = useState('')
  useEffect(() => { if (auth?.role) setSettingsRole(auth.role) }, [auth?.role])
  const visibleRole = settingsRole ?? auth?.role

  const refresh = async () => {
    setError('')
    try {
      if (kind === 'history') {
        const [history, alerts] = await Promise.all([api(titles[kind][2]), api('/api/v1/management/metrics/alerts')])
        setRows(history); setThresholds(alerts.thresholds)
      } else if (kind === 'storage') {
        const [backups, smart, system] = await Promise.all([api(titles[kind][2]), api('/api/v1/management/storage/smart'), api('/api/v1/system')])
        setRows(backups); setSavedTargets(backups); setSecondary(smart); setVolumes(system.disks)
      } else if (kind === 'security') {
        const [security, services] = await Promise.all([api(titles[kind][2]), api('/api/v1/management/services')])
        setRows([security]); setAllowlist(security.service_allowlist); setSecondary(services)
      } else if (kind === 'network') {
        const endpoints=await api(titles[kind][2]);setRows(endpoints);setSavedTargets(endpoints)
      } else if (kind === 'settings') {
        const profile = await api('/api/v1/auth/preferences')
        setSettingsRole(profile.role)
        onThemeChange?.(profile.theme)
        if (profile.role === 'admin') {
          const [users, permissionResponse] = await Promise.all([api(titles[kind][2]), api('/api/v1/management/users/permissions')])
          setUserRows(users); setAvailablePermissions(permissionResponse.permissions)
        } else {
          setUserRows([])
        }
      } else if (kind === 'audit') {
        const audit = await api(titles[kind][2])
        if (!Array.isArray(audit) || audit.some((row: Entry) => !row || typeof row !== 'object' || row.id == null || row.timestamp == null || row.username == null || row.action == null)) {
          throw new Error('The audit API returned an unexpected response. Confirm the dashboard and Synfull server are running the same version.')
        }
        setRows(audit)
      } else if (kind === 'logs') {
        setRows(await api(`${titles[kind][2]}?query=${encodeURIComponent(query)}`))
      } else setRows(await api(titles[kind][2]))
    } catch (cause) {
      if (kind === 'audit') setRows([])
      setError(cause instanceof Error ? cause.message : 'Unable to load this section')
    }
  }
  useEffect(() => {
    void refresh()
    const interval=window.setInterval(()=>void refresh(),kind==='history'?5000:60000)
    return()=>window.clearInterval(interval)
  }, [kind, auth?.role])
  useEffect(() => {
    if (kind !== 'history') return
    let active = true
    void api('/api/v1/management/metrics/discord').then(config => { if (active) setHistoryDiscord(config) })
      .catch(cause => { if (active) setHistoryDiscordError(cause instanceof Error ? cause.message : 'Unable to load webhook settings') })
    return () => { active = false }
  }, [kind])
  useEffect(() => {
    if (kind !== 'settings') return
    let active = true
    void api('/api/v1/settings').then(config => {
      if (active) {
        setInstalledVersion(config.version)
        setPackageUpdate(current => current ? { ...current, current_version: config.version } : {
          current_version: config.version, latest_version: '', update_available: false,
          update_command: 'npm install -g synfull@latest\nsynfull reload', package_url: 'https://www.npmjs.com/package/synfull',
        })
      }
    }).catch(() => {})
    return () => { active = false }
  }, [kind])
  useEffect(() => { if(kind==='logs')void refresh() }, [query])

  const save = async (path: string, body: unknown, success: string) => {
    setBusy(true); setError(''); setMessage('')
    try { await api(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); setMessage(success); await refresh() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to save settings') }
    finally { setBusy(false) }
  }
  const checkPackageUpdate = async () => {
    setUpdateCheckBusy(true); setUpdateCheckError(''); setMessage('')
    try {
      const result = await api('/api/v1/settings/update-check')
      setPackageUpdate(result)
      setMessage(result.update_available ? `Synfull ${result.latest_version} is available.` : `Synfull ${result.current_version} is up to date.`)
    } catch (cause) { setUpdateCheckError(cause instanceof Error ? cause.message : 'Could not check for updates') }
    finally { setUpdateCheckBusy(false) }
  }
  const saveHistoryDiscord = async () => {
    if (!historyDiscord) return
    setHistoryDiscordBusy(true); setHistoryDiscordError(''); setHistoryDiscordMessage('')
    try {
      const saved = await api('/api/v1/management/metrics/discord', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: historyDiscord.enabled, webhook_url: historyWebhookInput || null,
          webhook_username: historyDiscord.webhook_username, avatar_url: historyDiscord.avatar_url }) })
      setHistoryDiscord(saved); setHistoryWebhookInput(''); setHistoryDiscordMessage('History & Alerts webhook settings saved.')
    } catch (cause) { setHistoryDiscordError(cause instanceof Error ? cause.message : 'Could not save webhook settings') }
    finally { setHistoryDiscordBusy(false) }
  }
  const testHistoryDiscord = async () => {
    setHistoryDiscordBusy(true); setHistoryDiscordError(''); setHistoryDiscordMessage('')
    try {
      const result = await api('/api/v1/management/metrics/discord/test', { method: 'POST' })
      setHistoryDiscordMessage(result.message)
    } catch (cause) { setHistoryDiscordError(cause instanceof Error ? cause.message : 'Test message could not be sent') }
    finally { setHistoryDiscordBusy(false) }
  }
  const act = async (path: string, prompt: string) => {
    if (!window.confirm(prompt)) return
    setBusy(true); setError(''); setMessage('')
    try {
      if (kind === 'network' && path === '/api/v1/management/endpoints/check') {
        const saved = await api('/api/v1/management/endpoints', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ targets: rows.map(row => ({ name: row.name, url: row.url })) }),
        })
        setSavedTargets(saved.targets)
        setRows(saved.targets)
        const results = await api(path, { method: 'POST' })
        setRows(results)
        const healthy = results.filter((row: Entry) => row.status === 'healthy').length
        setMessage(`Checked ${results.length} endpoint(s): ${healthy} healthy, ${results.length - healthy} unhealthy.`)
      } else {
        await api(path, { method: 'POST' })
        setMessage('Action completed.')
        await refresh()
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Action failed') }
    finally { setBusy(false) }
  }
  const createManagedUser = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      await api('/api/v1/management/users', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: newUsername, password: newPassword, role: newRole, permissions: newRole === 'admin' ? ['*'] : newPermissions, theme: newTheme }) })
      setNewUsername(''); setNewPassword(''); setMessage('User account created.'); await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not create user') }
    finally { setBusy(false) }
  }
  const updateManagedUser = async (row: Entry, patch: Record<string, unknown>) => {
    setBusy(true); setError(''); setMessage('')
    try {
      const updated = await api(`/api/v1/management/users/${row.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: row.role, enabled: row.enabled, permissions: row.permissions, theme: row.theme, ...patch }) })
      setUserRows(current => current.map(user => user.id === updated.id ? updated : user)); setMessage('Account settings saved.')
      setPasswordResets(current => ({ ...current, [String(row.id)]: '' }))
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not update user') }
    finally { setBusy(false) }
  }
  const addTarget = () => {
    if (!name.trim() || !target.trim()) return
    const newRow = kind === 'network' ? { name: name.trim(), url: target.trim() } : { name: name.trim(), path: target.trim(), max_age_hours: freshness }
    setRows(current => [...current, newRow]); setName(''); setTarget('')
  }
  const removeTarget = (index:number,collection:'network'|'storage') => {
    const path=collection==='network'?'/api/v1/management/endpoints':'/api/v1/management/backups'
    const saved=savedTargets
    if(!rows[index]?.id){setRows(current=>current.filter((_,rowIndex)=>rowIndex!==index));return}
    const targets=saved.filter((_,rowIndex)=>rowIndex!==index).map(row=>collection==='network'?({name:row.name,url:row.url}):({name:row.name,path:row.path,max_age_hours:row.max_age_hours??48}))
    void save(path,{targets},collection==='network'?'Endpoint removed.':'Backup check removed.')
  }
  const [title, subtitle] = titles[kind]

  return <section className="route-page"><section className="page-heading"><div><h1>{title}</h1><p>{subtitle}</p></div><button className="console-button secondary-button" onClick={() => void refresh()} disabled={busy}><RefreshCw size={13}/> Refresh</button></section>
    {kind === 'network' && <section className="panel config-panel"><h2>Configure endpoint checks</h2><p className="muted">Only URLs you add here are checked. HTTPS certificate expiry is included.</p><div className="form-row"><input className="form-input" placeholder="Name, e.g. Main website" value={name} onChange={event => setName(event.target.value)}/><input className="form-input" placeholder="https://example.com/health" value={target} onChange={event => setTarget(event.target.value)}/><button className="console-button" onClick={addTarget}>Add</button></div><button className="console-button save-button" disabled={busy} onClick={() => void save('/api/v1/management/endpoints', { targets: rows.map(row => ({ name: row.name, url: row.url })) }, 'Endpoint checks saved.')}>Save targets</button><button className="console-button" disabled={busy||!rows.length} onClick={() => void act('/api/v1/management/endpoints/check', 'Check all configured endpoints now?')}>Check now</button></section>}
    {kind === 'storage' && <section className="panel config-panel"><h2>Configure backup freshness</h2><p className="muted">Monitor a file or directory; Synfull reports the newest file timestamp and does not run backup commands.</p><div className="form-row"><input className="form-input" placeholder="Backup name" value={name} onChange={event => setName(event.target.value)}/><input className="form-input" placeholder="Local file or directory path" value={target} onChange={event => setTarget(event.target.value)}/><input className="form-input small-input" type="number" min={1} max={8760} aria-label="Maximum backup age in hours" value={freshness} onChange={event => setFreshness(Number(event.target.value))}/><button className="console-button" onClick={addTarget}>Add</button></div><button className="console-button save-button" disabled={busy} onClick={() => void save('/api/v1/management/backups', { targets: rows.map(row => ({ name: row.name, path: row.path, max_age_hours: row.max_age_hours ?? 48 })) }, 'Backup checks saved.')}>Save backup checks</button></section>}
    {kind === 'security' && rows[0] && <section className="panel config-panel"><div className="security-summary"><ShieldCheck size={20}/>            <div><strong>{rows[0].is_local_only ? 'Local-only listener' : 'Network listener'}</strong><p className="muted">Listening on {rows[0].bind_host}:{rows[0].bind_port} · {rows[0].transport_is_https?'HTTPS':'HTTP (use HTTPS via a trusted reverse proxy for LAN access)'} · HttpOnly session cookie</p></div></div><h2>Service action allowlist</h2><p className="muted">Only services added here can be started, stopped, or restarted. Each action still asks for confirmation.</p><div className="form-row"><input className="form-input" placeholder="Service name / unit (e.g. nginx.service)" value={name} onChange={event => setName(event.target.value)}/><button className="console-button" onClick={() => { if(name.trim())setAllowlist(current=>[...new Set([...current,name.trim()])]);setName('') }}>Add service</button><button className="console-button save-button" disabled={busy} onClick={() => void save('/api/v1/management/services/allowlist',{services:allowlist},'Service allowlist saved.')}>Save allowlist</button></div><div className="chip-list">{allowlist.map(service=><button className="target-chip" key={service} onClick={() => setAllowlist(current=>current.filter(item=>item!==service))}>{service} ×</button>)}</div><h2>Available services</h2><EntryTable rows={secondary} columns={['name','display_name','status','startup_type','allowlisted']}/></section>}
    {kind === 'history' && <><section className="panel history-chart"><div className="panel-head"><div><h2>Resource history</h2><p>Stored in Synfull's local SQLite database; retention follows settings.</p></div><Activity size={17}/></div>{!rows.length&&<div className="unsupported">No samples have been stored yet. History begins as soon as the API is running.</div>}<div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={rows}><CartesianGrid stroke="#232d3c" vertical={false}/><XAxis dataKey="timestamp" tickFormatter={value=>new Date(Number(value)*1000).toLocaleTimeString()} tick={{fill:'#728096',fontSize:9}}/><YAxis domain={[0,100]} tick={{fill:'#728096',fontSize:9}} tickFormatter={value=>`${value}%`}/><Tooltip labelFormatter={value=>new Date(Number(value)*1000).toLocaleString()} contentStyle={{background:'#121b29',border:'1px solid #293548'}}/><Legend/><Area dataKey="cpu_percent" name="CPU" stroke="#8997ff" fill="#8997ff" fillOpacity={0.12} isAnimationActive={false}/><Area dataKey="memory_percent" name="Memory" stroke="#c097ff" fill="#c097ff" fillOpacity={0.08} isAnimationActive={false}/><Area dataKey="disk_percent" name="Disk" stroke="#55d7a1" fill="#55d7a1" fillOpacity={0.05} isAnimationActive={false}/></AreaChart></ResponsiveContainer></div></section>        <section className="panel config-panel"><h2>Alert thresholds</h2><p className="muted">Discord notifications are sent on threshold crossings and endpoint health issues using this page's independent webhook.</p><div className="threshold-grid">{Object.entries(thresholds).map(([key,value])=><label className="form-label" key={key}>{key.replace('_percent','').toUpperCase()}<div className="threshold-input"><input className="form-input" type="number" min={1} max={100} value={value} onChange={event=>setThresholds(current=>({...current,[key]:Number(event.target.value)}))}/><span>%</span></div></label>)}</div><button className="console-button save-button" disabled={busy} onClick={()=>void save('/api/v1/management/metrics/thresholds',{thresholds},'Thresholds saved.')}>Save thresholds</button></section>
        <section className="panel config-panel"><h2>History &amp; Alerts Discord webhook</h2><p className="muted">A separate webhook for metric threshold and endpoint health notifications. Docker container alerts use their own webhook.</p>
          {!historyDiscord?<div className="unsupported">Loading webhook settings…</div>:<>
            <div className="webhook-field"><label htmlFor="history-discord-webhook">Webhook URL</label><div className="webhook-input-row"><input id="history-discord-webhook" className="form-input" type="password" autoComplete="new-password" placeholder={historyDiscord.configured?historyDiscord.webhook_url_masked:'https://discord.com/api/webhooks/…'} value={historyWebhookInput} onChange={event=>setHistoryWebhookInput(event.target.value)}/><button className="console-button secondary-button" disabled={historyDiscordBusy||(!historyDiscord.configured&&!historyWebhookInput)} onClick={()=>void testHistoryDiscord()}>Send test</button></div><small>Stored locally and never returned by the API. Leave blank to keep the saved URL.</small></div>
            <div className="form-row"><label className="form-label">Webhook name<input className="form-input" maxLength={80} value={historyDiscord.webhook_username} onChange={event=>setHistoryDiscord(current=>current?{...current,webhook_username:event.target.value}:current)}/></label><label className="form-label">Avatar image URL<input className="form-input" type="url" placeholder="https://example.com/avatar.png" value={historyDiscord.avatar_url} onChange={event=>setHistoryDiscord(current=>current?{...current,avatar_url:event.target.value}:current)}/></label></div>
            <label className="toggle-control"><input type="checkbox" checked={historyDiscord.enabled} disabled={historyDiscordBusy||(!historyDiscord.configured&&!historyWebhookInput)} onChange={event=>setHistoryDiscord(current=>current?{...current,enabled:event.target.checked}:current)}/><span className="toggle-track"/><span className="toggle-label">{historyDiscord.enabled?'Enabled':'Disabled'}</span></label>
            <button className="console-button save-alert-button" disabled={historyDiscordBusy||!historyDiscord.webhook_username.trim()} onClick={()=>void saveHistoryDiscord()}>{historyDiscordBusy?'Saving…':'Save webhook settings'}</button>
          </>}{historyDiscordMessage&&<div className="alert-success" role="status">{historyDiscordMessage}</div>}{historyDiscordError&&<div className="alert console-alert" role="alert">{historyDiscordError}</div>}
        </section></>}

    {kind === 'services' && <div className="panel table-panel"><div className="panel-head"><div><h2>Host services</h2><p>Actions require explicit allowlisting in Security.</p></div></div><EntryTable rows={rows} columns={['name','display_name','status','startup_type','allowlisted']} renderAction={row=><div className="container-actions">{(['start','stop','restart'] as const).map(action=><button key={action} className="console-button" disabled={busy||!row.allowlisted} onClick={()=>void act(`/api/v1/management/services/${encodeURIComponent(String(row.name))}/${action}`,`${action[0].toUpperCase()+action.slice(1)} service ${row.name}?`)}>{action}</button>)}</div>}/></div>}
    {kind === 'jobs' && <section className="panel table-panel"><div className="panel-head"><div><h2>Scheduled jobs</h2><p>Read-only job and timer inventory.</p></div></div><EntryTable rows={rows} columns={['name','schedule','status','last_run','next_run','last_result']}/></section>}
    {kind === 'storage' && <><section className="info-grid storage-volume-grid">{volumes.map(volume=><article className="panel info-card" key={String(volume.mountpoint)}><div className="panel-head"><div><h2>{String(volume.mountpoint)}</h2><p>{String(volume.device??'Volume')} · {String(volume.filesystem??'')}</p></div></div><div className="volume-bar"><span style={{width:`${Math.min(100,Number(volume.total)?Number(volume.used)/Number(volume.total)*100:0)}%`}}/></div><div className="volume-caption">{(Number(volume.used)/1024**3).toFixed(1)} GB used · {(Number(volume.free)/1024**3).toFixed(1)} GB free of {(Number(volume.total)/1024**3).toFixed(1)} GB</div></article>)}</section><section className="panel table-panel"><div className="panel-head"><div><h2>Configured backup checks</h2><p>Freshness is determined from the newest file in each configured path.</p></div></div><EntryTable rows={rows} columns={['name','path','status','age_hours','max_age_hours','message']} renderAction={(_,index)=><button className="console-button" disabled={busy} onClick={()=>removeTarget(index,'storage')}>Remove</button>}/></section><section className="panel table-panel"><div className="panel-head"><div><h2>Disk health (SMART)</h2><p>Requires smartmontools and permissions to inspect disks.</p></div></div><EntryTable rows={secondary} columns={['device','status','message']}/></section></>}
    {kind === 'network' && <section className="panel table-panel"><div className="panel-head"><div><h2>Endpoint status</h2><p>Checks run every minute while Synfull is running.</p></div></div>{message&&<div className="alert-success" role="status">{message}</div>}{error&&<div className="alert" role="alert">{error}</div>}<EntryTable rows={rows} columns={['name','url','status','response_code','response_ms','tls_days_remaining','message']} renderAction={(_,index)=><button className="console-button" disabled={busy} onClick={()=>removeTarget(index,'network')}>Remove</button>}/></section>}
    {kind === 'settings' && <>
      <section className="panel config-panel"><h2>Synfull updates</h2><p className="muted">Installed version: <strong>{installedVersion}</strong>. Check npm for the latest published package version.</p><div className="update-actions"><button className="console-button secondary-button" disabled={updateCheckBusy} onClick={()=>void checkPackageUpdate()}>{updateCheckBusy?'Checking…':'Check for updates'}</button>{packageUpdate.package_url&&<a className="text-link" href={packageUpdate.package_url} target="_blank" rel="noreferrer">View npm package ↗</a>}</div>{packageUpdate.latest_version&&<p className="update-result">Latest npm version: <strong>{packageUpdate.latest_version}</strong>{packageUpdate.update_available?' · Update available':' · Up to date'}</p>}{updateCheckError&&<div className="alert" role="alert">{updateCheckError}</div>}<div className="update-command"><span>Update command</span><code>{packageUpdate.update_command}</code></div></section>
      <section className="panel config-panel"><h2>Dashboard theme</h2><p className="muted">Themes are saved to your account and follow you when you sign in.</p><div className="theme-grid">{[['synfull','Synfull Dark','The original glass dashboard theme.'],['monochrome','Monochrome','A restrained grayscale interface with subtle contrast.'],['hacker','Hacker Terminal','A high-contrast green terminal-inspired interface.'],['light','Light','A clean, bright interface for well-lit environments.'],['oled','OLED Black','Near-black surfaces for OLED displays.']].map(([id,label,description])=><button key={id} className={`theme-choice${auth?.theme===id?' selected':''}`} onClick={async()=>{setError('');try{await api('/api/v1/auth/preferences/theme',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({theme:id})});onThemeChange?.(id);setMessage(`${label} theme saved.`)}catch(cause){setError(cause instanceof Error?cause.message:'Could not save theme')}}}><strong>{label}</strong><span>{description}</span></button>)}</div></section>
      {visibleRole==='admin'&&<section className="panel config-panel"><h2>User accounts</h2><p className="muted">Create administrators or guests and configure what each guest can view or change.</p><div className="form-row"><input className="form-input" placeholder="Username" value={newUsername} onChange={event=>setNewUsername(event.target.value)}/><input className="form-input" type="password" placeholder="Temporary password (12+ characters)" value={newPassword} onChange={event=>setNewPassword(event.target.value)}/><select className="form-input" value={newRole} onChange={event=>setNewRole(event.target.value)}><option value="guest">Guest</option><option value="admin">Admin</option></select><select className="form-input" value={newTheme} onChange={event=>setNewTheme(event.target.value)}><option value="synfull">Synfull Dark</option><option value="monochrome">Monochrome</option><option value="hacker">Hacker Terminal</option><option value="light">Light</option><option value="oled">OLED Black</option></select><button className="console-button" disabled={busy||newUsername.trim().length===0||newPassword.length<12} onClick={()=>void createManagedUser()}>Create account</button></div>{newRole==='guest'&&<div className="permission-grid">{Object.entries(availablePermissions).map(([permission,label])=><label key={permission} className="alert-option"><input type="checkbox" checked={newPermissions.includes(permission)} onChange={event=>setNewPermissions(current=>event.target.checked?[...current,permission]:current.filter(item=>item!==permission))}/><span className="check-ui"/><span>{label}</span></label>)}</div>}</section>}
      {visibleRole==='admin'&&<section className="panel table-panel"><div className="panel-head"><div><h2>Existing accounts</h2><p>Disabling an account revokes its active sessions.</p></div></div><div className="user-list">{userRows.map(row=><article className="user-card" key={String(row.id)}><div className="user-card-heading"><strong>{String(row.username)}</strong><span>{String(row.role)} · {row.enabled?'Enabled':'Disabled'}</span></div><div className="form-row"><select className="form-input" value={String(row.role)} onChange={event=>void updateManagedUser(row,{role:event.target.value,permissions:event.target.value==='admin'?['*']:row.role==='admin'?[]:row.permissions})}><option value="admin">Admin</option><option value="guest">Guest</option></select><select className="form-input" value={String(row.theme)} onChange={event=>void updateManagedUser(row,{theme:event.target.value})}><option value="synfull">Synfull Dark</option><option value="monochrome">Monochrome</option><option value="hacker">Hacker Terminal</option><option value="light">Light</option><option value="oled">OLED Black</option></select><input className="form-input" type="password" placeholder="New password (optional)" value={passwordResets[String(row.id)]??''} onChange={event=>setPasswordResets(current=>({...current,[String(row.id)]:event.target.value}))}/><button className="console-button" disabled={busy||Boolean(passwordResets[String(row.id)]&&passwordResets[String(row.id)].length<12)} onClick={()=>void updateManagedUser(row,{enabled:!row.enabled,password:passwordResets[String(row.id)]||undefined})}>{row.enabled?'Disable / save':'Enable / save'}</button></div>{row.role==='guest'&&<div className="permission-grid">{Object.entries(availablePermissions).map(([permission,label])=><label key={permission} className="alert-option"><input type="checkbox" checked={(row.permissions as string[]).includes(permission)} onChange={event=>void updateManagedUser(row,{permissions:event.target.checked?[...(row.permissions as string[]),permission]:(row.permissions as string[]).filter(item=>item!==permission)})}/><span className="check-ui"/><span>{label}</span></label>)}</div>}</article>)}</div></section>}
      {visibleRole!=='admin'&&<section className="panel config-panel"><h2>User accounts</h2><p className="muted">Account management is available to administrator accounts only.</p></section>}
    </>}
    {kind === 'audit' && <section className="panel table-panel"><div className="panel-head"><div><h2>Administrator activity</h2><p>Latest 200 events, newest first.</p></div></div>{error && <div className="alert" role="alert">{error}</div>}<EntryTable rows={rows} columns={['timestamp','username','action','target','outcome','details']}/></section>}
    {kind === 'logs' && <section className="panel table-panel"><div className="panel-head"><div><h2>Host event records</h2><p>Search matches the retrieved recent entries.</p></div></div><div className="form-row log-search"><input className="form-input" placeholder="Filter logs…" value={query} onChange={event=>setQuery(event.target.value)}/></div><EntryTable rows={rows} columns={['timestamp','level','source','message']}/></section>}

    {message && <div className="alert-success" role="status"><Check size={13}/> {message}</div>}{error && <div className="alert" role="alert">{error}</div>}
  </section>
}

function EntryTable({ rows, columns, renderAction }: { rows: Entry[]; columns: string[]; renderAction?: (row: Entry, index: number) => React.ReactNode }) {
  return <div className="table-scroll"><table><thead><tr>{columns.map(column=><th key={column}>{column.replace(/_/g,' ')}</th>)}{renderAction&&<th>ACTION</th>}</tr></thead><tbody>{rows.map((row,index)=><tr key={String(row.id ?? row.name ?? index)}>{columns.map(column=><td key={column}>{column==='timestamp'&&typeof row[column]==='number'?new Date(Number(row[column])*1000).toLocaleString():String(row[column]??'—')}</td>)}{renderAction&&<td>{renderAction(row,index)}</td>}</tr>)}</tbody></table>{!rows.length&&<div className="empty">No entries yet.</div>}</div>
}
