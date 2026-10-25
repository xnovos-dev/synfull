import { useCallback, useEffect, useState } from 'react'
import { Activity, Bell, Box, Cpu, FileClock, HardDrive, History, ListChecks, Logs, MemoryStick, Network, Server, Settings2, Shield, ShieldCheck, TerminalSquare } from 'lucide-react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { AuthInfo, AuthPage, ManagementPage } from './ManagementPages'

type System = {
  platform: string; hostname: string; cpu_percent: number | null; per_cpu_percent: number[] | null
  memory_total: number | null; memory_used: number | null; memory_available: number | null
  swap_total: number | null; swap_used: number | null; disks: {device:string;mountpoint:string;total:number;used:number;free:number}[]
  networks: {name:string;bytes_sent:number;bytes_received:number}[]; uptime_seconds:number|null
  load_average:number[]|null; temperatures:{sensor:string;label:string;current:number}[]|null
}
type Process = {pid:number;name:string|null;username:string|null;cpu_percent:number|null;memory_bytes:number|null;executable:string|null;command_line:string[]|null;parent_pid:number|null;status:string|null}
type DockerContainer = {id:string;name:string;image:string;state:string;cpu_percent:string|number|null;memory_usage:string|null;memory_percent:string|number|null}
type Integration = {installed:boolean;available:boolean;status:string;message:string;running?:number;containers?:DockerContainer[];processes?:{id:number|string;name:string;status:string;cpu_percent:number|null;memory_bytes:number|null;uptime_ms:number|null}[]}
type ContainerLogs = {id:string;name:string;logs:string;tail:number}
type DiscordAlerts = {enabled:boolean;configured:boolean;webhook_url_masked:string;webhook_username:string;avatar_url:string;events:{start:boolean;stop:boolean;crash:boolean;restart:boolean};minimum_interval_seconds:number}
type AppSettings = {bind_host:string;bind_port:number;state_dir:string;database_path:string;log_dir:string;collection_interval_seconds:number;metric_retention_days:number;development:boolean;docker_endpoint_configured:boolean;pm2_executable_configured:boolean;pm2_owner:string|null;version:string}
const formatBytes = (value: number | null) => value == null ? 'Unavailable' : `${(value / 1024 ** 3).toFixed(1)} GB`
const percent = (value: number | null) => value == null ? 'Unavailable' : `${value.toFixed(1)}%`

export default function App() {
  const [auth, setAuth] = useState<AuthInfo|null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [system, setSystem] = useState<System | null>(null)
  const [processes, setProcesses] = useState<Process[]>([])
  const [processesLoading, setProcessesLoading] = useState(true)
  const [initialLoading, setInitialLoading] = useState(true)
  const [error, setError] = useState('')
  const [integrations, setIntegrations] = useState<{docker:Integration;pm2:Integration}|null>(null)
  const [settings, setSettings] = useState<AppSettings|null>(null)
  const [history, setHistory] = useState<{time:string; cpu:number}[]>([])
  const location = useLocation()
  const refreshAuth = useCallback(async()=>{
    setAuthLoading(true)
    try{
      const response=await fetch('/api/v1/auth/status')
      if(!response.ok)throw new Error('Could not verify administrator session')
      const status: AuthInfo=await response.json()
      if(status.authenticated && !status.must_change_password){
        const profileResponse=await fetch('/api/v1/auth/preferences')
        if(profileResponse.ok){const profile=await profileResponse.json();status.role=profile.role;status.theme=profile.theme;status.permissions=profile.permissions}
      }
      setAuth(status)
    }
    catch(cause){setError(cause instanceof Error?cause.message:'Authentication check failed')}
    finally{setAuthLoading(false)}
  },[])
  useEffect(()=>{void refreshAuth()},[refreshAuth])
  useEffect(()=>{document.documentElement.dataset.theme=auth?.theme??'synfull'},[auth?.theme])
  useEffect(() => {
    if(!auth?.authenticated)return
    let active = true
    let updating = false
    const update = async () => {
      if (updating) return
      updating = true
      try {
        const response = await fetch('/api/v1/system')
        if (!response.ok) throw new Error('The monitoring API is unavailable')
        const next: System = await response.json()
        if (!active) return
        setSystem(next); setError(''); setInitialLoading(false)
        setHistory(old => [...old, { time: new Date().toLocaleTimeString(), cpu: next.cpu_percent ?? 0 }].slice(-24))
      } catch (cause) { if (active) { setError(cause instanceof Error ? cause.message : 'Unable to load metrics'); setInitialLoading(false) } }
      finally { updating = false }
    }
    void update()
    const timer = window.setInterval(update, 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [auth?.authenticated])
  useEffect(() => {
    if(!auth?.authenticated || !['/', '/processes'].includes(location.pathname))return
    let active = true
    let updating = false
    let initialLoadFinished = false
    const updateProcesses = async () => {
      if (updating) return
      updating = true
      try {
        const response = await fetch('/api/v1/processes')
        if (!response.ok) throw new Error('Could not load process data')
        const running: Process[] = await response.json()
        if (active) { setProcesses(running); setProcessesLoading(false); initialLoadFinished = true }
      } catch (cause) {
        if (active) { setError(cause instanceof Error ? cause.message : 'Could not load process data'); setProcessesLoading(false); initialLoadFinished = true }
      } finally { updating = false }
    }
    const initialTimer = window.setTimeout(() => void updateProcesses(), initialLoadFinished ? 0 : 1000)
    const timer = window.setInterval(updateProcesses, 15000)
    return () => { active = false; window.clearTimeout(initialTimer); window.clearInterval(timer) }
  }, [auth?.authenticated, location.pathname])
  useEffect(() => {
    if (!auth?.authenticated || !system) return
    let active = true
    void fetch('/api/v1/integrations').then(response => response.ok ? response.json() : Promise.reject(new Error('Could not load integrations')))
      .then(value => { if (active) setIntegrations(value) })
      .catch(() => {})
    return () => { active = false }
  }, [auth?.authenticated, Boolean(system)])
  useEffect(() => {
    if (!auth?.authenticated || location.pathname !== '/system-info') return
    let active = true
    void fetch('/api/v1/settings').then(response => response.ok ? response.json() : Promise.reject(new Error('Could not load settings')))
      .then(value => { if (active) setSettings(value) })
      .catch(() => {})
    return () => { active = false }
  }, [auth?.authenticated, location.pathname])
  useEffect(()=>{if(auth?.authenticated)setError('')},[location.pathname,auth?.authenticated])
  const memPercent = system?.memory_total && system.memory_used != null ? system.memory_used / system.memory_total * 100 : null
  if(authLoading)return <main className="auth-screen"><div className="panel auth-card">Checking secure session…</div></main>
  if(!auth?.authenticated || auth.must_change_password)return <AuthPage auth={auth??{setup_required:false,authenticated:false,username:null,setup_instructions:null}} onSuccess={()=>void refreshAuth()}/>
  const signOut=async()=>{await fetch('/api/v1/auth/logout',{method:'POST'});setAuth(current=>current?{...current,authenticated:false,username:null}:current)}
  const redirectToSignIn=()=>{setAuth(current=>current?{...current,authenticated:false,username:null}:current)}
  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><div className="brand-icon"><Activity size={21}/></div><span>synfull</span></div>
      <div className="nav-label">MONITORING</div>
      <NavLink end className={({isActive})=>`nav-item${isActive?' active':''}`} to="/"><Server size={17}/><span>Overview</span></NavLink>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/processes"><TerminalSquare size={17}/><span>Processes</span></NavLink>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/system-info"><Settings2 size={17}/><span>System Info</span></NavLink>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/history"><History size={17}/><span>History & Alerts</span></NavLink>
      <div className="nav-label integration-label">OPERATIONS</div>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/services"><ListChecks size={17}/><span>Services</span></NavLink>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/jobs"><FileClock size={17}/><span>Scheduled jobs</span></NavLink>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/network"><Network size={17}/><span>Network</span></NavLink>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/storage"><HardDrive size={17}/><span>Storage & backups</span></NavLink>
      <div className="nav-label integration-label">SECURITY</div>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/security"><Shield size={17}/><span>Security</span></NavLink>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/audit"><ShieldCheck size={17}/><span>Audit log</span></NavLink>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/logs"><Logs size={17}/><span>System logs</span></NavLink>
      <NavLink className={({isActive})=>`nav-item${isActive?' active':''}`} to="/settings"><Settings2 size={17}/><span>Settings</span></NavLink>
      {(integrations?.pm2.installed || integrations?.docker.installed) && <div className="nav-label integration-label">INTEGRATIONS</div>}
      {integrations?.pm2.installed && <NavLink className={({isActive})=>`nav-item integration-nav${isActive?' active':''}`} to="/pm2"><Activity size={17}/><span>PM2</span><span className={`nav-badge ${integrations.pm2.available?'online':''}`}>{integrations.pm2.available ? integrations.pm2.running ?? 0 : '!'}</span></NavLink>}
      {integrations?.docker.installed && <NavLink className={({isActive})=>`nav-item integration-nav${isActive?' active':''}`} to="/docker"><Box size={17}/><span>Docker</span><span className={`nav-badge ${integrations.docker.available?'online':''}`}>{integrations.docker.available ? integrations.docker.running ?? 0 : '!'}</span></NavLink>}
      <div className="sidebar-bottom"><span className="live-dot"/>Agent online <small>{system?.platform ?? 'Connecting'}</small></div>
    </aside>
    <main className="main"><header className="topbar"><div><span className="eyebrow">INFRASTRUCTURE /</span> <span>{system?.hostname ?? 'Host overview'}</span></div><div className="topbar-actions"><span className="connection"><span className="live-dot"/>{error ? 'Connection issue' : 'Live monitoring'}</span><span className="user-chip">{auth?.username}</span><button className="console-button" onClick={()=>void signOut()}>Sign out</button></div></header>
      {error && <div className="alert">{error}. Ensure the Synfull API is running on port 8000.</div>}
      <Routes><Route path="/" element={<>
        <section className="page-heading"><div><h1>System overview</h1><p>Live health and performance for this host.</p></div><div className="host-pill"><span className="live-dot"/>{system?.hostname ?? 'Discovering host'}</div></section>
        {initialLoading&&!system?<DashboardSkeleton/>:<>
        <section className="metric-grid">
          <Metric icon={<Cpu/>} title="CPU usage" value={percent(system?.cpu_percent ?? null)} note={`${system?.per_cpu_percent?.length ?? '—'} logical cores`} tone="blue"/>
          <Metric icon={<MemoryStick/>} title="Memory" value={memPercent == null ? 'Unavailable' : `${memPercent.toFixed(1)}%`} note={`${formatBytes(system?.memory_used ?? null)} of ${formatBytes(system?.memory_total ?? null)}`} tone="violet"/>
          <Metric icon={<HardDrive/>} title="Storage" value={system?.disks.length ? `${((system.disks.reduce((sum,d) => sum+d.used,0)/Math.max(1,system.disks.reduce((sum,d) => sum+d.total,0)))*100).toFixed(1)}%` : 'Unavailable'} note={`${system?.disks.length ?? 0} accessible volumes`} tone="green"/>
          <Metric icon={<Network/>} title="Network interfaces" value={`${system?.networks.length ?? '—'}`} note="Host interfaces detected" tone="amber"/>
        </section>
        <section className="content-grid"><article className="panel chart-panel"><div className="panel-head"><div><h2>CPU performance</h2><p>Recent utilization</p></div><span className="live-tag">● LIVE</span></div><div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={history}><defs><linearGradient id="fillCpu" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#7c8cff" stopOpacity={0.35}/><stop offset="100%" stopColor="#7c8cff" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="#232d3c" vertical={false}/><XAxis dataKey="time" tick={{fill:'#728096',fontSize:10}} axisLine={false} tickLine={false}/><YAxis domain={[0,100]} tick={{fill:'#728096',fontSize:10}} axisLine={false} tickLine={false} tickFormatter={v=>`${v}%`}/><Tooltip contentStyle={{background:'#121b29',border:'1px solid #293548',borderRadius:8}}/><Area type="monotone" dataKey="cpu" stroke="#8997ff" strokeWidth={2} fill="url(#fillCpu)" isAnimationActive={false}/></AreaChart></ResponsiveContainer></div></article>
          <article className="panel details-panel"><div className="panel-head"><div><h2>Host details</h2><p>System information</p></div></div><Detail label="Operating system" value={system?.platform ?? '—'}/><Detail label="Uptime" value={system?.uptime_seconds == null ? 'Unavailable' : `${Math.floor(system.uptime_seconds / 86400)} days, ${Math.floor(system.uptime_seconds % 86400 / 3600)} hours`}/><Detail label="Swap used" value={system?.swap_total ? `${formatBytes(system.swap_used)} / ${formatBytes(system.swap_total)}` : 'Unavailable'}/><Detail label="Load average" value={system?.load_average?.map(x=>x.toFixed(2)).join(' · ') ?? 'Unsupported'}/><Detail label="Temperature sensors" value={system?.temperatures == null ? 'Unsupported' : `${system.temperatures.length} available`}/></article></section>
        <section className="panel table-panel"><div className="panel-head"><div><h2>Top processes</h2><p>Live process inspection</p></div><NavLink className="text-link" to="/processes">View all →</NavLink></div><ProcessTable rows={[...processes].sort((a,b)=>(b.cpu_percent??0)-(a.cpu_percent??0)).slice(0,6)}/></section>
        </>}
      </>}/>      <Route path="/processes" element={<section className="route-page"><section className="page-heading"><div><h1>Processes</h1><p>Running processes and resource usage.</p></div><span className="host-pill">{processesLoading?'Loading…':`${processes.length} processes`}</span></section><section className="panel table-panel process-full"><div className="panel-head"><div><h2>Running processes</h2><p>Sorted by CPU usage · refreshes every 15 seconds</p></div></div>{processesLoading?<ProcessSkeleton/>:<ProcessTable rows={[...processes].sort((a,b)=>(b.cpu_percent??0)-(a.cpu_percent??0))}/>}</section></section>}/>
      <Route path="/system-info" element={<SystemInfoPage system={system} settings={settings}/>}/>
      <Route path="/history" element={<ManagementPage kind="history"/>}/>
      <Route path="/services" element={<ManagementPage kind="services"/>}/>
      <Route path="/jobs" element={<ManagementPage kind="jobs"/>}/>
      <Route path="/network" element={<ManagementPage kind="network"/>}/>
      <Route path="/storage" element={<ManagementPage kind="storage"/>}/>
      <Route path="/security" element={<ManagementPage kind="security"/>}/>
      <Route path="/audit" element={<ManagementPage kind="audit"/>}/>
      <Route path="/logs" element={<ManagementPage kind="logs"/>}/>
      <Route path="/settings" element={<ManagementPage kind="settings" auth={auth??undefined} onThemeChange={theme=>setAuth(current=>current?{...current,theme}:current)}/>}/>
      <Route path="/pm2" element={<IntegrationPage title="PM2 monitoring" type="PM2" integration={integrations?.pm2 ?? null}/>}/>
      <Route path="/docker" element={<IntegrationPage title="Docker monitoring" type="Docker" integration={integrations?.docker ?? null} onRefresh={async()=>{const response=await fetch('/api/v1/integrations');if(response.ok)setIntegrations(await response.json())}} onDockerAction={async(id,action)=>{const response=await fetch(`/api/v1/docker/containers/${encodeURIComponent(id)}/${action}`,{method:'POST'});if(!response.ok)throw new Error(await response.text());const refreshed=await fetch('/api/v1/integrations');if(refreshed.ok)setIntegrations(await refreshed.json())}}/>}/>
      </Routes>
      <footer>Synfull <span>Cross-platform host monitoring</span></footer>
    </main>
  </div>
}
function SystemInfoPage({system,settings}:{system:System|null;settings:AppSettings|null}) {
  if(!system)return <section className="route-page"><section className="page-heading"><div><h1>System information</h1><p>Host hardware, operating system, storage and Synfull configuration.</p></div></section><SystemInfoSkeleton/></section>
  return <section className="route-page"><section className="page-heading"><div><h1>System information</h1><p>Host hardware, operating system, storage and Synfull configuration.</p></div><div className="host-pill"><span className="live-dot"/>{system?.hostname ?? 'Loading host'}</div></section>
    <div className="info-grid">
      <article className="panel info-card"><div className="panel-head"><div><h2>Host & operating system</h2><p>Machine identity and runtime</p></div><Server size={18}/></div><Detail label="Hostname" value={system?.hostname ?? 'Unavailable'}/><Detail label="Operating system" value={system?.platform ?? 'Unavailable'}/><Detail label="Uptime" value={system?.uptime_seconds == null ? 'Unavailable' : formatDuration(system.uptime_seconds)}/><Detail label="CPU threads" value={system?.per_cpu_percent ? `${system.per_cpu_percent.length} logical processors` : 'Unavailable'}/><Detail label="CPU load average" value={system?.load_average?.map(value=>value.toFixed(2)).join(' · ') ?? 'Unsupported on this platform'}/></article>
      <article className="panel info-card"><div className="panel-head"><div><h2>Memory</h2><p>Physical and virtual memory</p></div><MemoryStick size={18}/></div><Detail label="Physical total" value={formatBytes(system?.memory_total ?? null)}/><Detail label="Physical used" value={formatBytes(system?.memory_used ?? null)}/><Detail label="Available" value={formatBytes(system?.memory_available ?? null)}/><Detail label="Swap total" value={formatBytes(system?.swap_total ?? null)}/><Detail label="Swap used" value={formatBytes(system?.swap_used ?? null)}/></article>
      <article className="panel info-card"><div className="panel-head"><div><h2>Storage volumes</h2><p>{system?.disks.length ?? 0} accessible volumes</p></div><HardDrive size={18}/></div>{system?.disks.map(disk=><div className="volume-card" key={`${disk.device}-${disk.mountpoint}`}><div className="volume-title"><strong>{disk.mountpoint}</strong><span>{disk.device || 'Volume'}</span></div><div className="volume-bar"><span style={{width:`${disk.total ? Math.min(100,disk.used/disk.total*100) : 0}%`}}/></div><div className="volume-caption">{formatBytes(disk.used)} used of {formatBytes(disk.total)} · {formatBytes(disk.free)} free</div></div>)}{system && system.disks.length===0&&<div className="empty">No accessible volumes reported.</div>}</article>
      <article className="panel info-card"><div className="panel-head"><div><h2>Network interfaces</h2><p>Host interface counters</p></div><Network size={18}/></div>{system?.networks.map(network=><div className="network-card" key={network.name}><strong>{network.name}</strong><Detail label="Received" value={formatBytes(network.bytes_received)}/><Detail label="Sent" value={formatBytes(network.bytes_sent)}/></div>)}{system && system.networks.length===0&&<div className="empty">No interfaces reported.</div>}</article>
      <article className="panel info-card"><div className="panel-head"><div><h2>Temperature sensors</h2><p>Platform sensors, when available</p></div><Activity size={18}/></div>{system?.temperatures == null?<div className="unsupported">Temperature sensors are not supported or available on this host.</div>:system.temperatures.length===0?<div className="unsupported">No temperature sensors detected.</div>:system.temperatures.map(sensor=><Detail key={`${sensor.sensor}-${sensor.label}`} label={`${sensor.sensor} · ${sensor.label}`} value={`${sensor.current.toFixed(1)} °C`}/>)}</article>
      <article className="panel info-card"><div className="panel-head"><div><h2>Synfull configuration</h2><p>Runtime paths and collection settings</p></div><Settings2 size={18}/></div><Detail label="Synfull version" value={settings?.version ?? 'Unavailable'}/><Detail label="Listen address" value={settings?`${settings.bind_host}:${settings.bind_port}`:'Unavailable'}/><Detail label="State directory" value={settings?.state_dir ?? 'Unavailable'}/><Detail label="Database" value={settings?.database_path ?? 'Unavailable'}/><Detail label="Log directory" value={settings?.log_dir ?? 'Unavailable'}/><Detail label="Collection interval" value={settings?`${settings.collection_interval_seconds} seconds`:'Unavailable'}/><Detail label="Retention policy" value={settings?`${settings.metric_retention_days} days`:'Unavailable'}/></article>
    </div>
  </section>
}
function IntegrationPage({title,type,integration,onRefresh,onDockerAction}:{title:string;type:'PM2'|'Docker';integration:Integration|null;onRefresh?:()=>Promise<void>;onDockerAction?:(id:string,action:'start'|'stop'|'restart')=>Promise<void>}) {
  const available=integration?.available ?? false
  const running=integration?.running ?? 0
  const [selectedContainer,setSelectedContainer]=useState<DockerContainer|null>(null)
  const [logs,setLogs]=useState<ContainerLogs|null>(null)
  const [logsBusy,setLogsBusy]=useState(false)
  const [actionBusy,setActionBusy]=useState<string|null>(null)
  const [consoleError,setConsoleError]=useState('')
  const [alertConfig,setAlertConfig]=useState<DiscordAlerts|null>(null)
  const [webhookInput,setWebhookInput]=useState('')
  const [alertsBusy,setAlertsBusy]=useState(false)
  const [alertsMessage,setAlertsMessage]=useState('')
  const [alertsError,setAlertsError]=useState('')
  useEffect(()=>{
    if(type!=='Docker')return
    let active=true
    fetch('/api/v1/docker/alerts/discord').then(async response=>{if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(body?.detail??body?.error?.message??`Unable to load Discord alert settings (HTTP ${response.status})`)}return response.json()}).then(config=>{if(active)setAlertConfig(config)}).catch(cause=>{if(active)setAlertsError(cause instanceof Error?cause.message:'Unable to load alert settings')})
    return()=>{active=false}
  },[type])
  const saveAlerts=async()=>{
    if(!alertConfig)return
    setAlertsBusy(true);setAlertsError('');setAlertsMessage('')
    try{
      const response=await fetch('/api/v1/docker/alerts/discord',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:alertConfig.enabled,events:alertConfig.events,minimum_interval_seconds:alertConfig.minimum_interval_seconds,webhook_url:webhookInput||null,webhook_username:alertConfig.webhook_username,avatar_url:alertConfig.avatar_url})})
      const body=await response.json();if(!response.ok)throw new Error(body.detail??'Could not save Discord alert settings')
      setAlertConfig(body);setWebhookInput('');setAlertsMessage('Discord alert settings saved.')
    }catch(cause){setAlertsError(cause instanceof Error?cause.message:'Could not save Discord alert settings')}
    finally{setAlertsBusy(false)}
  }
  const testAlerts=async()=>{
    setAlertsBusy(true);setAlertsError('');setAlertsMessage('')
    try{const response=await fetch('/api/v1/docker/alerts/discord/test',{method:'POST'});const body=await response.json();if(!response.ok)throw new Error(body.detail??'Test message could not be sent');setAlertsMessage(body.message)}
    catch(cause){setAlertsError(cause instanceof Error?cause.message:'Test message could not be sent')}
    finally{setAlertsBusy(false)}
  }
  const loadLogs=useCallback(async(container:DockerContainer,quiet=false)=>{
    setSelectedContainer(container);if(!quiet)setLogsBusy(true);setConsoleError('')
    try{const response=await fetch(`/api/v1/docker/containers/${encodeURIComponent(container.id)}/logs?tail=300`);if(!response.ok)throw new Error((await response.json()).detail??'Unable to retrieve container logs');setLogs(await response.json())}
    catch(cause){setConsoleError(cause instanceof Error?cause.message:'Unable to retrieve container logs')}
    finally{if(!quiet)setLogsBusy(false)}
  },[])
  useEffect(()=>{
    if(type!=='Docker'||!selectedContainer)return
    const timer=window.setInterval(()=>void loadLogs(selectedContainer,true),3000)
    return()=>window.clearInterval(timer)
  },[type,selectedContainer,loadLogs])
  const performAction=async(container:DockerContainer,action:'start'|'stop'|'restart')=>{
    const verb=action==='restart'?'Restart':action[0].toUpperCase()+action.slice(1)
    if(!window.confirm(`${verb} container “${container.name}”?`))return
    setActionBusy(container.id);setConsoleError('')
    try{await onDockerAction?.(container.id,action);const updated=integration?.containers?.find(item=>item.id===container.id);if(updated&&selectedContainer?.id===container.id)await loadLogs(updated)}
    catch(cause){setConsoleError(cause instanceof Error?cause.message:'Container action failed')}
    finally{setActionBusy(null)}
  }
  return <section className="route-page"><section className="page-heading"><div><h1>{title}</h1><p>Live status and resource metrics from the local {type} integration.</p></div><span className={`integration-state ${available?'state-online':'state-offline'}`}><span className="live-dot"/>{available?'Connected':integration?.installed?'Installed · unavailable':'Detecting'}</span></section>
    {!integration?<IntegrationSkeleton/>:!integration.installed?<div className="panel integration-message"><div className="metric-icon amber">{type==='Docker'?<Box/>:<Activity/>}</div><h2>{type} is not installed</h2><p>Synfull checks integrations independently. Install and configure {type} to see live status here.</p></div>:!available?<div className="panel integration-message"><div className="metric-icon amber">{type==='Docker'?<Box/>:<Activity/>}</div><h2>{type} is installed but not reachable</h2><p>{integration.message}</p></div>:<>
      <section className="metric-grid integration-metrics"><Metric icon={type==='Docker'?<Box/>:<Activity/>} title="Running" value={`${running}`} note={`of ${type==='Docker'?'containers':'processes'} running`} tone="green"/><Metric icon={<Activity/>} title="Integration" value="Connected" note="Status refreshes every 5 seconds" tone="blue"/></section>
      <section className="panel table-panel integration-table"><div className="panel-head"><div><h2>{type==='Docker'?'Containers':'PM2 processes'}</h2><p>{integration[type==='Docker'?'containers':'processes']?.length ?? 0} entries reported by {type}</p></div><button className="console-button secondary-button" onClick={()=>void onRefresh?.()}>Refresh status</button></div>
      {type==='Docker'?<div className="table-scroll"><table><thead><tr><th>CONTAINER</th><th>ID</th><th>IMAGE</th><th>STATE</th><th>CPU</th><th>MEMORY</th><th>ACTIONS</th></tr></thead><tbody>{(integration.containers??[]).map(container=><tr key={container.id}><td className="process-name"><button className="container-link" onClick={()=>void loadLogs(container)}>{container.name}</button></td><td>{container.id}</td><td>{container.image}</td><td><span className={`status ${container.state==='running'?'':'status-stopped'}`}>{container.state}</span></td><td>{container.cpu_percent==null?'—':typeof container.cpu_percent==='number'?`${container.cpu_percent.toFixed(1)}%`:container.cpu_percent}</td><td>{container.memory_usage??'—'}{container.memory_percent!=null?` (${container.memory_percent}%)`:''}</td><td><div className="container-actions"><button className="console-button" disabled={actionBusy===container.id||container.state==='running'} onClick={()=>void performAction(container,'start')}>Start</button><button className="console-button" disabled={actionBusy===container.id||container.state!=='running'} onClick={()=>void performAction(container,'stop')}>Stop</button><button className="console-button" disabled={actionBusy===container.id||container.state!=='running'} onClick={()=>void performAction(container,'restart')}>Restart</button></div></td></tr>)}</tbody></table>{!(integration.containers?.length)&&<div className="empty">No containers found.</div>}</div>:<div className="table-scroll"><table><thead><tr><th>PROCESS</th><th>ID</th><th>STATUS</th><th>CPU</th><th>MEMORY</th></tr></thead><tbody>{(integration.processes??[]).map(process=><tr key={process.id}><td className="process-name">{process.name}</td><td>{process.id}</td><td><span className={`status ${process.status==='online'?'':'status-stopped'}`}>{process.status}</span></td><td>{process.cpu_percent==null?'Unavailable':`${process.cpu_percent.toFixed(1)}%`}</td><td>{formatBytes(process.memory_bytes)}</td></tr>)}</tbody></table>{!(integration.processes?.length)&&<div className="empty">No PM2 processes found.</div>}</div>}
      {type==='Docker'&&<section className="discord-alert-panel"><div className="discord-alert-heading"><div className="metric-icon violet"><Bell size={16}/></div><div><h3>Discord webhook alerts</h3><p>Choose which Docker container events send notifications to Discord.</p></div><label className="toggle-control"><input type="checkbox" checked={alertConfig?.enabled??false} disabled={!alertConfig||alertsBusy||(!alertConfig.configured&&!webhookInput)} onChange={event=>setAlertConfig(current=>current?{...current,enabled:event.target.checked}:current)}/><span className="toggle-track"/><span className="toggle-label">{alertConfig?.enabled?'Enabled':'Disabled'}</span></label></div>
        {!alertConfig?<div className="unsupported">Loading alert configuration…</div>:<>
          <div className="webhook-field"><label htmlFor="discord-webhook">Webhook URL</label><div className="webhook-input-row"><input id="discord-webhook" type="password" autoComplete="new-password" placeholder={alertConfig.configured?alertConfig.webhook_url_masked:'https://discord.com/api/webhooks/…'} value={webhookInput} onChange={event=>setWebhookInput(event.target.value)}/><button className="console-button secondary-button" onClick={()=>void testAlerts()} disabled={alertsBusy||(!alertConfig.configured&&!webhookInput)}>Send test</button></div><small>The URL is stored locally and never returned in API responses. Leave blank to keep the saved URL.</small></div>
          <div className="form-row"><label className="form-label">Webhook name<input className="form-input" maxLength={80} value={alertConfig.webhook_username} onChange={event=>setAlertConfig(current=>current?{...current,webhook_username:event.target.value}:current)}/></label><label className="form-label">Avatar image URL<input className="form-input" type="url" placeholder="https://example.com/avatar.png" value={alertConfig.avatar_url} onChange={event=>setAlertConfig(current=>current?{...current,avatar_url:event.target.value}:current)}/></label></div>
          <div className="alert-options"><span className="alert-options-title">Notify me when a container</span>{([['start','Starts'],['stop','Stops'],['crash','Crashes unexpectedly'],['restart','Restarts']] as const).map(([key,label])=><label className="alert-option" key={key}><input type="checkbox" checked={alertConfig.events[key]} onChange={(event: React.ChangeEvent<HTMLInputElement>)=>setAlertConfig(current=>current?{...current,events:{...current.events,[key]:event.target.checked}}:current)}/><span className="check-ui"/><span>{label}</span></label>)}</div>
          <div className="alert-form-footer"><label className="throttle-field">Minimum repeat interval <select value={alertConfig.minimum_interval_seconds} onChange={event=>setAlertConfig(current=>current?{...current,minimum_interval_seconds:Number(event.target.value)}:current)}><option value={0}>No limit</option><option value={30}>30 seconds</option><option value={60}>1 minute</option><option value={300}>5 minutes</option><option value={900}>15 minutes</option></select></label><button className="console-button save-alert-button" onClick={()=>void saveAlerts()} disabled={alertsBusy}>{alertsBusy?'Saving…':'Save alert settings'}</button></div>
        </>}{alertsMessage&&<div className="alert-success" role="status">{alertsMessage}</div>}{alertsError&&<div className="alert console-alert" role="alert">{alertsError}</div>}</section>}
      {type==='Docker'&&<div className="docker-console"><div className="console-header"><div><h3>{selectedContainer?`${selectedContainer.name} · console`: 'Container console'}</h3><p>{selectedContainer?'Recent output (latest 300 lines)':'Select a container name to inspect its logs.'}</p></div>{selectedContainer&&<button className="console-button secondary-button" disabled={logsBusy} onClick={()=>void loadLogs(selectedContainer)}>Refresh logs</button>}</div>{consoleError&&<div className="alert console-alert">{consoleError}</div>}{!selectedContainer?<div className="console-placeholder">Choose a container above to view its recent logs.</div>:logsBusy?<div className="console-placeholder">Loading container logs…</div>:<pre className="log-output">{logs?.logs||'No log output returned.'}</pre>}</div>}</section>
    </>}
  </section>
}
function SkeletonBlock({className=''}:{className?:string}){return <div aria-hidden="true" className={`skeleton ${className}`}/>}
function DashboardSkeleton(){return <div className="dashboard-skeleton" aria-label="Loading system metrics"><div className="skeleton-metric-grid">{[0,1,2,3].map(item=><div className="skeleton-card metric-skeleton" key={item}><SkeletonBlock className="skeleton-icon"/><SkeletonBlock className="skeleton-line short"/><SkeletonBlock className="skeleton-line value-line"/><SkeletonBlock className="skeleton-line medium"/></div>)}</div><div className="skeleton-content-grid"><div className="skeleton-card chart-skeleton"><SkeletonBlock className="skeleton-line medium"/><SkeletonBlock className="skeleton-chart"/></div><div className="skeleton-card detail-skeleton"><SkeletonBlock className="skeleton-line medium"/>{[0,1,2,3,4].map(item=><SkeletonBlock className="skeleton-line full" key={item}/>)}</div></div><div className="skeleton-card table-skeleton"><SkeletonBlock className="skeleton-line medium"/>{[0,1,2].map(item=><SkeletonBlock className="skeleton-row" key={item}/>)}</div></div>}
function ProcessSkeleton(){return <div className="skeleton-card process-skeleton">{[0,1,2,3,4,5,6].map(row=><div className="skeleton-row" key={row}><SkeletonBlock className="skeleton-line medium"/><SkeletonBlock className="skeleton-line short"/><SkeletonBlock className="skeleton-line medium"/></div>)}</div>}
function SystemInfoSkeleton(){return <div className="info-grid">{[0,1,2,3].map(card=><div className="skeleton-card info-skeleton" key={card}><SkeletonBlock className="skeleton-line medium"/>{[0,1,2,3,4].map(row=><SkeletonBlock className="skeleton-line full" key={row}/>)}</div>)}</div>}
function IntegrationSkeleton(){return <div className="dashboard-skeleton"><div className="skeleton-metric-grid two-columns">{[0,1].map(item=><div className="skeleton-card metric-skeleton" key={item}><SkeletonBlock className="skeleton-icon"/><SkeletonBlock className="skeleton-line short"/><SkeletonBlock className="skeleton-line value-line"/></div>)}</div><div className="skeleton-card table-skeleton"><SkeletonBlock className="skeleton-line medium"/>{[0,1,2,3].map(item=><SkeletonBlock className="skeleton-row" key={item}/>)}</div></div>}
function formatDuration(seconds:number){const days=Math.floor(seconds/86400);const hours=Math.floor(seconds%86400/3600);const minutes=Math.floor(seconds%3600/60);return [days?`${days}d`:null,hours?`${hours}h`:null,`${minutes}m`].filter(Boolean).join(' ')}
function Metric({icon,title,value,note,tone}:{icon:React.ReactNode;title:string;value:string;note:string;tone:string}){return <article className="metric-card"><div className={`metric-icon ${tone}`}>{icon}</div><div className="metric-title">{title}</div><div className="metric-value">{value}</div><div className="metric-note">{note}</div></article>}
function Detail({label,value}:{label:string;value:string}){return <div className="detail-row"><span>{label}</span><strong>{value}</strong></div>}
function ProcessTable({rows}:{rows:Process[]}){return <div className="table-scroll"><table><thead><tr><th>Process</th><th>PID</th><th>USER</th><th>CPU</th><th>MEMORY</th><th>STATUS</th></tr></thead><tbody>{rows.map(p=><tr key={p.pid}><td className="process-name">{p.name ?? 'Unknown'}<small>{p.executable ?? 'Executable unavailable'}</small></td><td>{p.pid}</td><td>{p.username ?? '—'}</td><td>{percent(p.cpu_percent)}</td><td>{formatBytes(p.memory_bytes)}</td><td><span className="status">{p.status ?? 'unknown'}</span></td></tr>)}</tbody></table>{rows.length===0&&<div className="empty">Waiting for process data…</div>}</div>}
