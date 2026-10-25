'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { reloadServer } = require('./synfull.cjs')

async function testBackgroundReload() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'synfull-cli-test-'))
  const pidFile = path.join(directory, 'synfull.pid')
  fs.writeFileSync(pidFile, '1234')
  const calls = []
  await reloadServer({
    windows: false,
    pidFile,
    spawnSync: command => { calls.push(`probe:${command}`); return { status: 1 } },
    processExists: () => true,
    stopServer: () => calls.push('stop'),
    waitForProcessExit: async pid => calls.push(`wait:${pid}`),
    ensureRuntime: () => calls.push('refresh'),
    startServer: () => calls.push('start'),
  })
  assert.deepEqual(calls, ['probe:systemctl', 'stop', 'wait:1234', 'refresh', 'start'])
  fs.rmSync(directory, { recursive: true, force: true })
}

async function testSystemdServiceReload() {
  const calls = []
  const spawnSync = (_command, args) => {
    const key = args.join(' ')
    if (key === 'cat synfull-npm.service') return { status: 0 }
    if (key === 'is-active --quiet synfull-npm.service') return { status: 0 }
    return { status: 1 }
  }
  await reloadServer({
    windows: false,
    spawnSync,
    run: (command, args) => calls.push(`run:${command} ${args.join(' ')}`),
    ensureRuntime: () => calls.push('refresh'),
    startServer: () => calls.push('unexpected-start'),
  })
  assert.deepEqual(calls, ['run:sudo systemctl stop synfull-npm.service', 'refresh', 'run:sudo systemctl start synfull-npm.service'])
}

async function testScheduledTaskReload() {
  const calls = []
  await reloadServer({
    windows: true,
    spawnSync: () => ({ status: 0, stdout: 'Status: Ready\n' }),
    run: (command, args) => calls.push(`${command} ${args.join(' ')}`),
    ensureRuntime: () => calls.push('refresh'),
    startScheduledTask: () => calls.push('start-task'),
  })
  assert.deepEqual(calls, ['refresh', 'start-task'])
}

async function main() {
  await testBackgroundReload()
  await testSystemdServiceReload()
  await testScheduledTaskReload()
  console.log('CLI reload tests passed.')
}

main().catch(error => {
  console.error(error)
  process.exitCode = 1
})
