#!/usr/bin/env node
'use strict'

const crypto = require('node:crypto')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const readline = require('node:readline/promises')
const { spawn, spawnSync } = require('node:child_process')
const ROOT = path.resolve(__dirname, '..')
const VERSION = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8')).version
const WINDOWS = process.platform === 'win32'
function targetAccountHome() {
  if (WINDOWS || !process.env.SUDO_USER) return os.homedir()
  const account = spawnSync('getent', ['passwd', process.env.SUDO_USER], { encoding: 'utf8' })
  return account.status === 0 && account.stdout.trim().split(':')[5] || os.homedir()
}
function effectiveHome() {
  if (process.env.SYNFULL_HOME) return process.env.SYNFULL_HOME
  const userHome = targetAccountHome()
  return WINDOWS
    ? path.join(process.env.LOCALAPPDATA || path.join(userHome, 'AppData', 'Local'), 'Synfull')
    : path.join(process.env.XDG_DATA_HOME || path.join(userHome, '.local', 'share'), 'synfull')
}
const HOME = effectiveHome()
const APP_DIR = path.join(HOME, 'app')
const STATE_DIR = process.env.SYNFULL_STATE_DIR || (WINDOWS
  ? path.join(process.env.LOCALAPPDATA || path.join(targetAccountHome(), 'AppData', 'Local'), 'Synfull')
  : path.join(process.env.XDG_STATE_HOME || path.join(targetAccountHome(), '.local', 'state'), 'synfull'))
const RUNTIME = path.join(APP_DIR, '.venv', WINDOWS ? 'Scripts' : 'bin', WINDOWS ? 'python.exe' : 'python')
const PID_FILE = path.join(HOME, 'synfull.pid')
const LOG_FILE = path.join(HOME, 'synfull.log')
const CLI_CONFIG = path.join(HOME, 'config.json')
const CLI = path.resolve(__filename)

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: 'inherit', windowsHide: true, ...options })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`${command} exited with status ${result.status ?? 'unknown'}`)
}
function pythonCommand() {
  const candidates = WINDOWS ? [['py', ['-3']], ['python', []], ['python3', []]] : [['python3', []], ['python', []]]
  for (const [command, prefix] of candidates) {
    const probe = spawnSync(command, [...prefix, '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'], { encoding: 'utf8', windowsHide: true })
    if (probe.status === 0) {
      const [major, minor] = probe.stdout.trim().split('.').map(Number)
      if (major > 3 || (major === 3 && minor >= 10)) return { command, prefix }
    }
  }
  throw new Error('Python 3.10+ was not found. Install Python and ensure it is on PATH, then run `synfull setup`.')
}
function configuredBindHost() {
  try {
    const config = JSON.parse(fs.readFileSync(CLI_CONFIG, 'utf8'))
    if (config.bindHost === '0.0.0.0') return '0.0.0.0'
  } catch {}
  return '127.0.0.1'
}
function selectedBindHost() {
  const host = process.env.SYNFULL_BIND_HOST || configuredBindHost()
  if (!['127.0.0.1', '0.0.0.0'].includes(host)) throw new Error('SYNFULL_BIND_HOST must be 127.0.0.1 or 0.0.0.0.')
  return host
}
function saveBindHost(host) {
  if (!['127.0.0.1', '0.0.0.0'].includes(host)) throw new Error('Bind host must be 127.0.0.1 or 0.0.0.0.')
  fs.mkdirSync(HOME, { recursive: true })
  fs.writeFileSync(CLI_CONFIG, `${JSON.stringify({ bindHost: host }, null, 2)}\n`, { mode: 0o600 })
}
async function configureBindHost(rl) {
  if (!process.stdin.isTTY || !process.stdout.isTTY) return
  const answer = await rl.question('Allow other devices on your LAN to access Synfull by binding to 0.0.0.0? (y/N): ')
  const host = /^y(es)?$/i.test(answer.trim()) ? '0.0.0.0' : '127.0.0.1'
  saveBindHost(host)
  console.log(host === '0.0.0.0'
    ? 'LAN access enabled. Change the initial admin password from this machine before signing in from another device; use a trusted LAN and configure your firewall.'
    : 'Synfull will only accept connections from this machine.')
}
async function configureDockerAccess(rl, options = {}) {
  const platform = options.platform || process.platform
  const interactive = options.interactive ?? Boolean(process.stdin.isTTY && process.stdout.isTTY)
  const username = options.username || process.env.SUDO_USER || os.userInfo().username
  const isRoot = options.isRoot ?? Boolean(process.getuid && process.getuid() === 0)
  const spawnCommand = options.spawnSync || spawnSync
  const runCommand = options.run || run
  if (platform !== 'linux' || !interactive) return false
  if (isRoot) {
    console.log('Run `synfull setup` as your normal account (not sudo); setup will request sudo only if Docker group access is approved.')
    return false
  }
  const dockerGroup = spawnCommand('getent', ['group', 'docker'], { encoding: 'utf8', windowsHide: true })
  if (dockerGroup.status !== 0) {
    console.log('No Linux docker group was found. Install/configure Docker Engine first; Synfull did not change account permissions.')
    return false
  }
  console.log('Docker group membership grants root-equivalent control of this computer (including control over containers and host files).')
  const answer = await rl.question(`Allow Synfull to use Docker by adding ${username} to the docker group? (y/N): `)
  if (!/^y(es)?$/i.test(answer.trim())) {
    console.log('Docker group access was not changed. The Docker integration will remain unavailable unless your account already has socket access.')
    return false
  }
  const groups = spawnCommand('id', ['-nG', username], { encoding: 'utf8', windowsHide: true })
  const alreadyMember = groups.status === 0 && groups.stdout.trim().split(/\s+/).includes('docker')
  if (!alreadyMember) {
    const command = process.getuid && process.getuid() === 0 ? 'usermod' : 'sudo'
    const args = command === 'sudo' ? ['usermod', '-aG', 'docker', username] : ['-aG', 'docker', username]
    runCommand(command, args)
    console.log(`Added ${username} to the docker group.`)
  } else {
    console.log(`${username} is already in the docker group.`)
  }
  const serviceInstalled = spawnCommand('systemctl', ['cat', 'synfull-npm.service'], { stdio: 'ignore', windowsHide: true }).status === 0
  if (!serviceInstalled) console.log('No Synfull systemd service is installed. Log out/in (or start a fresh session with Docker group membership) before running Synfull so it receives the new group permissions.')
  return serviceInstalled
}
function restartSynfullService(runCommand = run) {
  runCommand('sudo', ['systemctl', 'restart', 'synfull-npm.service'])
  console.log('Restarted the Synfull system service so it picks up Docker group access.')
}
async function configureSetup() {
  if (!process.stdin.isTTY || !process.stdout.isTTY) return false
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout })
  try {
    await configureBindHost(rl)
    return await configureDockerAccess(rl)
  } finally {
    rl.close()
  }
}
function ensureRuntime() {
  if (!fs.existsSync(path.join(ROOT, 'backend', 'app')) || !fs.existsSync(path.join(ROOT, 'frontend', 'dist', 'index.html'))) {
    throw new Error('This npm package is missing its backend or built dashboard assets.')
  }
  fs.mkdirSync(HOME, { recursive: true })
  fs.mkdirSync(STATE_DIR, { recursive: true })
  fs.mkdirSync(APP_DIR, { recursive: true })
  for (const name of ['backend', 'frontend']) {
    fs.cpSync(path.join(ROOT, name), path.join(APP_DIR, name), { recursive: true, force: true })
  }
  if (!fs.existsSync(RUNTIME)) {
    const python = pythonCommand()
    run(python.command, [...python.prefix, '-m', 'venv', path.join(APP_DIR, '.venv')])
  }
  const pipAvailable = spawnSync(RUNTIME, ['-m', 'pip', '--version'], { stdio: 'ignore', windowsHide: true }).status === 0
  if (!pipAvailable) {
    const python = pythonCommand()
    const repair = spawnSync(python.command, [...python.prefix, '-m', 'venv', '--upgrade-deps', '--upgrade', path.join(APP_DIR, '.venv')], { encoding: 'utf8', windowsHide: true })
    if (repair.status !== 0) {
      const detail = (repair.stderr || repair.stdout || '').trim()
      if (detail) console.error(detail)
      const platformFix = WINDOWS
        ? 'Repair or reinstall Python with its pip/venv components, then rerun `synfull setup`.'
        : 'On Ubuntu/Debian install venv support (`sudo apt install python3-venv`, or the matching `python3.X-venv` package), remove the incomplete venv at ~/.local/share/synfull/app/.venv, then rerun `synfull setup`.'
      throw new Error(`The Python virtual environment is missing pip. ${platformFix}`)
    }
  }
  const pipReady = spawnSync(RUNTIME, ['-m', 'pip', '--version'], { stdio: 'ignore', windowsHide: true }).status === 0
  if (!pipReady) throw new Error('The Python virtual environment still has no pip. Install the Python venv support package and recreate the runtime, then rerun `synfull setup`.')
  const requirementsPath = path.join(APP_DIR, 'backend', 'requirements-runtime.txt')
  const requirementsHash = crypto.createHash('sha256').update(fs.readFileSync(requirementsPath)).digest('hex')
  const installedHashPath = path.join(APP_DIR, '.runtime-requirements-hash')
  const installedHash = fs.existsSync(installedHashPath) ? fs.readFileSync(installedHashPath, 'utf8').trim() : ''
  const dependenciesReady = spawnSync(RUNTIME, ['-c', 'import uvicorn, fastapi, pydantic_settings, sqlalchemy, psutil, docker'], { stdio: 'ignore', windowsHide: true }).status === 0
  if (!dependenciesReady || installedHash !== requirementsHash) {
    run(RUNTIME, ['-m', 'pip', 'install', '--disable-pip-version-check', '-r', requirementsPath])
    fs.writeFileSync(installedHashPath, requirementsHash)
  }
  run(RUNTIME, ['-c', 'import uvicorn, fastapi, pydantic_settings, sqlalchemy, psutil, docker; import app.main'], {
    cwd: APP_DIR,
    env: { ...serverEnvironment(), PYTHONPATH: path.join(APP_DIR, 'backend') },
  })
}
function serverEnvironment() {
  return {
    ...process.env,
    SYNFULL_BIND_HOST: selectedBindHost(),
    SYNFULL_BIND_PORT: process.env.SYNFULL_BIND_PORT || '8000',
    SYNFULL_STATE_DIR: STATE_DIR,
    SYNFULL_BOOTSTRAP_DEFAULT_ADMIN: '1',
    PYTHONPATH: path.join(APP_DIR, 'backend'),
  }
}
function startServer(foreground) {
  ensureRuntime()
  const env = serverEnvironment()
  const bindHost = env.SYNFULL_BIND_HOST
  const port = process.env.SYNFULL_BIND_PORT || '8000'
  const args = ['-m', 'uvicorn', 'app.main:app', '--app-dir', path.join(APP_DIR, 'backend'), '--host', bindHost, '--port', port]
  if (foreground) {
    const child = spawn(RUNTIME, args, { cwd: APP_DIR, env, stdio: 'inherit', windowsHide: true })
    child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)))
    for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal))
    return
  }
  if (fs.existsSync(PID_FILE)) {
    const pid = Number(fs.readFileSync(PID_FILE, 'utf8'))
    try { process.kill(pid, 0); console.log(`Synfull is already running (PID ${pid}).`); return } catch { fs.rmSync(PID_FILE, { force: true }) }
  }
  const output = fs.openSync(LOG_FILE, 'a')
  const child = spawn(RUNTIME, args, { cwd: APP_DIR, env, detached: !WINDOWS, stdio: ['ignore', output, output], windowsHide: true })
  fs.closeSync(output)
  child.once('error', error => {
    fs.rmSync(PID_FILE, { force: true })
    console.error(`Synfull failed to start: ${error.message}`)
    process.exitCode = 1
  })
  child.once('spawn', () => {
    fs.writeFileSync(PID_FILE, String(child.pid))
    console.log(`Synfull started on ${bindHost}:${port} (PID ${child.pid}).`)
    if (bindHost === '0.0.0.0') console.log(`LAN clients can use http://<server-lan-ip>:${port}; change the initial admin password locally first.`)
  })
  child.unref()
  console.log(`Logs: ${LOG_FILE}`)
  console.log('First login: admin / admin; Synfull will require a new password before proceeding.')
}
function stopServer() {
  if (!fs.existsSync(PID_FILE)) { console.log('Synfull is not running.'); return }
  const pid = Number(fs.readFileSync(PID_FILE, 'utf8'))
  if (WINDOWS) {
    const result = spawnSync('taskkill.exe', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
    if (result.status !== 0) console.log('Synfull process was already stopped.')
  } else {
    try { process.kill(-pid, 'SIGTERM') } catch { try { process.kill(pid, 'SIGTERM') } catch { console.log('Synfull process was already stopped.') } }
  }
  fs.rmSync(PID_FILE, { force: true })
  console.log('Synfull stopped.')
}
function status() {
  let running = false
  let pid = ''
  if (fs.existsSync(PID_FILE)) {
    pid = fs.readFileSync(PID_FILE, 'utf8').trim()
    try { process.kill(Number(pid), 0); running = true } catch { fs.rmSync(PID_FILE, { force: true }) }
  }
  const bindHost = selectedBindHost()
  console.log(`Synfull ${VERSION}: ${running ? `running (PID ${pid})` : 'stopped'}`)
  console.log(`Dashboard: http://${bindHost === '0.0.0.0' ? '<server-lan-ip>' : '127.0.0.1'}:${process.env.SYNFULL_BIND_PORT || '8000'}`)
  console.log(`Data: ${STATE_DIR}`)
}
function windowsTaskXml() {
  const identity = spawnSync('whoami.exe', ['/user', '/fo', 'csv', '/nh'], { encoding: 'utf8', windowsHide: true })
  const sid = (identity.stdout || '').match(/S-1-[0-9-]+/)
  if (!sid) throw new Error('Could not determine the current Windows account SID.')
  const exe = process.execPath.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  const cli = CLI.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  return `<?xml version="1.0" encoding="UTF-16"?>\n<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Triggers><LogonTrigger><Enabled>true</Enabled><UserId>${sid[0]}</UserId></LogonTrigger></Triggers><Principals><Principal id="Author"><UserId>${sid[0]}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Enabled>true</Enabled></Settings><Actions Context="Author"><Exec><Command>${exe}</Command><Arguments>"${cli}" run</Arguments><WorkingDirectory>${APP_DIR.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</WorkingDirectory></Exec></Actions></Task>`
}
function installStartup() {
  if (!WINDOWS && process.getuid && process.getuid() !== 0) throw new Error('Installing the system-wide boot service requires administrator permission. Re-run with sudo; the service will still run as your original user.')
  if (!WINDOWS && (process.env.SUDO_USER || os.userInfo().username) === 'root') throw new Error('Do not install Synfull as root. Re-run `sudo synfull install-service` from your normal account.')
  saveBindHost(selectedBindHost())
  ensureRuntime()
  const bindHost = selectedBindHost()
  const port = process.env.SYNFULL_BIND_PORT || '8000'
  if (WINDOWS) {
    const xmlPath = path.join(os.tmpdir(), `synfull-task-${process.pid}.xml`)
    fs.writeFileSync(xmlPath, `\ufeff${windowsTaskXml()}`, 'utf16le')
    try { run('schtasks.exe', ['/Create', '/TN', 'Synfull', '/XML', xmlPath, '/F']) } finally { fs.rmSync(xmlPath, { force: true }) }
    console.log(`Synfull will start at sign-in and restart after failures. Bind address: ${bindHost}.`)
  } else {
    const serviceUser = process.env.SUDO_USER || os.userInfo().username
    fs.mkdirSync(STATE_DIR, { recursive: true })
    run('chown', ['-R', serviceUser, HOME, STATE_DIR])
    const escaped = value => value.replace(/\\/g, '\\\\').replace(/ /g, '\\x20').replace(/%/g, '%%')
    const unit = `[Unit]\nDescription=Synfull host monitoring\nAfter=network.target\n\n[Service]\nType=simple\nUser=${serviceUser}\nWorkingDirectory=${escaped(APP_DIR)}\nEnvironment=SYNFULL_BIND_HOST=${bindHost}\nEnvironment=SYNFULL_BIND_PORT=${port}\nEnvironment=SYNFULL_STATE_DIR=${escaped(STATE_DIR)}\nEnvironment=SYNFULL_BOOTSTRAP_DEFAULT_ADMIN=1\nEnvironment=PYTHONPATH=${escaped(path.join(APP_DIR, 'backend'))}\nExecStart=${escaped(RUNTIME)} -m uvicorn app.main:app --app-dir ${escaped(path.join(APP_DIR, 'backend'))} --host ${bindHost} --port ${port}\nRestart=always\nRestartSec=3\n\n[Install]\nWantedBy=multi-user.target\n`
    const unitPath = '/etc/systemd/system/synfull-npm.service'
    fs.writeFileSync(unitPath, unit, { mode: 0o644 })
    try { run('systemctl', ['daemon-reload']); run('systemctl', ['enable', '--now', 'synfull-npm.service']) }
    catch (error) { fs.rmSync(unitPath, { force: true }); throw error }
    console.log(`Synfull is installed as a systemd service and starts at boot. Bind address: ${bindHost}.`)
  }
  if (bindHost === '0.0.0.0') console.log('Allow access only from trusted LAN devices, configure your firewall, and change the initial admin password locally before remote sign-in.')
}
function uninstallStartup() {
  if (WINDOWS) run('schtasks.exe', ['/Delete', '/TN', 'Synfull', '/F'])
  else { run('systemctl', ['disable', '--now', 'synfull-npm.service']); fs.rmSync('/etc/systemd/system/synfull-npm.service', { force: true }); run('systemctl', ['daemon-reload']) }
  console.log('Synfull startup registration removed; application data was retained.')
}
function help() {
  console.log(`Synfull ${VERSION}\n\nUsage: synfull <command>\n\nCommands:\n  setup             Prepare local runtime and install Python dependencies\n  start             Start Synfull in the background\n  run               Run Synfull in the foreground (for service managers)\n  stop              Stop a background Synfull process\n  status            Show status and local URLs\n  open              Open the local dashboard\n  install-service   Register start-at-login (Windows) or start-at-boot (Linux)\n  uninstall-service Remove startup registration without deleting data\n\nSetup asks whether to allow LAN connections; localhost-only is the default. Use a trusted LAN, open the port in your firewall only as needed, and change the initial admin password locally before remote sign-in.`)
}
async function main() {
  const command = process.argv[2]
  if (command === '--version' || command === '-v') { console.log(VERSION); return }
  if (command === '--help' || command === '-h') { help(); return }
  if (!['win32', 'linux'].includes(process.platform)) throw new Error('Synfull npm CLI currently supports Windows and Linux.')
  if (command === 'setup') {
    const restartServiceForDocker = await configureSetup()
    ensureRuntime()
    if (restartServiceForDocker) restartSynfullService()
    console.log(`Runtime ready. Data directory: ${STATE_DIR}`)
  }
  else if (command === 'start') startServer(false)
  else if (command === 'run') startServer(true)
  else if (command === 'stop') stopServer()
  else if (command === 'status') status()
  else if (command === 'open') {
    const host = selectedBindHost()
    const url = `http://${host === '0.0.0.0' ? '127.0.0.1' : host}:${process.env.SYNFULL_BIND_PORT || '8000'}`
    if (WINDOWS) spawn('cmd.exe', ['/c', 'start', '', url], { detached: true, stdio: 'ignore', windowsHide: true }).unref()
    else spawn('xdg-open', [url], { detached: true, stdio: 'ignore' }).unref()
  }
  else if (command === 'install-service') installStartup()
  else if (command === 'uninstall-service') uninstallStartup()
  else help()
}
if (require.main === module) main().catch(error => { console.error(`Synfull: ${error.message}`); process.exitCode = 1 })
module.exports = { ensureRuntime, serverEnvironment, configuredBindHost, selectedBindHost, saveBindHost, configureDockerAccess, restartSynfullService, APP_DIR, STATE_DIR, ROOT }
