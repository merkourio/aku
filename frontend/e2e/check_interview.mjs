// M9 check: interview mode in the browser UI.
//
// Records your voice sample in Settings, records a short interview with the microphone only,
// checks that you are labelled by yourself and the guests can be named, and saves the Markdown.
// Uses a silent virtual device: nothing plays on real speakers, no real mic is used.
//
// Run it against a throwaway service, because it enrols a test voice as "yours":
//   cd backend && AKU_SESSIONS_DIR=/tmp/aku-m9/s AKU_DB=/tmp/aku-m9/db AKU_CONFIG_DIR=/tmp/aku-m9/c uv run aku serve --port 4597 --no-tray
//   cd frontend && node e2e/check_interview.mjs http://127.0.0.1:4597
// Needs ../scripts/check_interview.py to have been run once (it builds the interview recordings).

import { execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromium } from 'playwright'

const base = process.argv[2] ?? 'http://127.0.0.1:4597'
const audio = join(homedir(), '.cache', 'aku-test')
const shots = join(process.cwd(), '..', 'tmp', 'shots')
mkdirSync(shots, { recursive: true })
const outDir = mkdtempSync(join(tmpdir(), 'aku-ui-'))

const pactl = (...args) => execFileSync('pactl', args, { encoding: 'utf8' }).trim()
const modules = [
  pactl('load-module', 'module-null-sink', 'sink_name=aku_check_room', 'sink_properties=device.description=AkuCheckRoomFeed'),
  pactl('load-module', 'module-remap-source', 'master=aku_check_room.monitor', 'source_name=aku_check_room_src', 'source_properties=device.description=AkuCheckRoom'),
]
const players = []
const play = (file) => players.push(spawn('pw-play', ['--target', 'aku_check_room', join(audio, file)], { stdio: 'ignore' }))
const results = []
const check = (name, ok, extra = '') => {
  results.push(ok)
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${name}${extra ? ' - ' + extra : ''}`)
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1180, height: 900 } })
const shot = (name) => page.screenshot({ path: join(shots, `${name}.png`), fullPage: true })
page.on('pageerror', (e) => check('no page errors', false, e.message))
let session = null

try {
  // the voice sample is recorded with the microphone of the last recording
  await page.goto(base + '/')
  await page.evaluate(async () => {
    const s = await (await fetch('/settings')).json()
    await fetch('/settings', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ last_sources: { ...s.last_sources, input: 'aku_check_room_src' } }),
    })
  })

  // 1. Settings: record my voice
  await page.goto(base + '/#/settings')
  const panel = page.locator('[data-panel="my-voice"]')
  await panel.getByText('Not recorded yet.').waitFor()
  play('interview_me.wav')
  await panel.getByRole('button', { name: /Record my voice/ }).click()
  await panel.getByText(/keep reading aloud/).waitFor()
  await shot('m9-1-enrol')
  await panel.getByText(/Saved for the built-in method/).waitFor({ timeout: 90_000 })
  check('my voice is recorded from Settings', true)
  players.pop().kill()

  // 2. Record: interview mode, microphone only
  await page.goto(base + '/#/')
  await page.getByRole('heading', { name: 'Record a call' }).waitFor()
  await page.waitForFunction(() => [...document.querySelectorAll('option')].some((o) => o.textContent.includes('AkuCheckRoom')))
  check('a normal call is the default', !(await page.getByLabel(/Interview mode/).isChecked()))
  await page.getByLabel(/Interview mode/).check()
  check('computer audio is left out by default',
    (await page.getByLabel('Everything playing on an output device').count()) === 0 &&
    !(await page.getByLabel(/Also record the computer's audio/).isChecked()))
  check('no warning about an unknown voice once it is recorded', (await page.getByText(/does not know your voice yet/).count()) === 0)
  await page.locator('.panel select').first().selectOption({ label: 'AkuCheckRoom' })
  await shot('m9-2-record')
  play('interview_room.wav')
  await page.getByRole('button', { name: /Start recording/ }).click()
  await page.getByRole('heading', { name: /Recording/ }).waitFor()
  check('only the microphone count is offered while recording',
    (await page.getByLabel('People at the microphone').count()) === 1 && (await page.getByLabel('Other speakers so far').count()) === 0)

  const who = () => page.locator('.line .who').allTextContents()
  await page.waitForFunction(() => {
    const names = [...document.querySelectorAll('.line .who')].map((e) => e.textContent)
    return names.some((n) => n.startsWith('Guest')) && names.some((n) => n.startsWith('Me'))
  }, null, { timeout: 120_000 })
  check('live: guests and me are told apart', true, [...new Set(await who())].sort().join(' '))
  await page.waitForFunction(() => new Set([...document.querySelectorAll('.line .who')].map((e) => e.textContent).filter((n) => n.startsWith('Guest'))).size >= 2, null, { timeout: 120_000 })
  await shot('m9-3-live')
  await page.getByLabel('People at the microphone').selectOption('3')
  await page.waitForFunction(async () => (await (await fetch('/status')).json()).live?.mic?.speakers === 3)
  check('the number of people at the microphone can be set during the recording', true)

  await page.getByRole('button', { name: /Stop/ }).click()
  await page.getByRole('heading', { name: 'Who was talking?' }).waitFor({ timeout: 120_000 })
  session = await page.evaluate(() => location.hash.split('/').pop())
  const group = page.locator('[data-group="mic"]')
  await group.getByText('(you)').waitFor()
  const guests = group.locator('input[aria-label^="Name for"]')
  check('I am labelled by my voice, the guests are left to name',
    (await group.getByText('(you)').count()) === 1 && (await guests.count()) === 2 && (await page.locator('[data-group="system"]').count()) === 0,
    `${await guests.count()} guests`)
  check('nobody is asked which voice is mine', (await page.getByText(/Which of these voices is yours/).count()) === 0)
  const clip = await page.request.get(`${base}/sessions/${session}/speakers/M1/clip`)
  check('a clip from the microphone is playable', clip.ok() && (await clip.body()).length > 20_000)
  const typed = ['Anna Test', 'Bo Test']
  for (let i = 0; i < (await guests.count()); i++) {
    await guests.nth(i).fill(typed[i])
    await guests.nth(i).blur()
  }
  await page.waitForFunction(() => document.querySelector('.transcript')?.textContent.includes('Bo Test'))

  // "This is me" on another speaker moves my name there, and back
  const radios = group.getByLabel('This is me')
  const mine = await radios.evaluateAll((els) => els.findIndex((e) => e.checked))
  const other = mine === 0 ? 1 : 0
  await radios.nth(other).check()
  await page.waitForFunction((i) => document.querySelectorAll('[data-group="mic"] input[type=radio]')[i]?.checked, other)
  await group.getByText('(you)').waitFor()
  check('"This is me" can be moved to another speaker', (await group.getByText('(you)').count()) === 1)
  await radios.nth(mine).check()
  await page.waitForFunction((i) => document.querySelectorAll('[data-group="mic"] input[type=radio]')[i]?.checked, mine)

  await page.getByLabel('Title').fill('UI check interview')
  await page.getByLabel('Folder').fill(outDir)
  await shot('m9-4-naming')
  await page.getByRole('button', { name: 'Finalise and save Markdown' }).click()
  await page.getByText(/Saved as/).waitFor()
  const savedAs = (await page.getByText(/Saved as/).textContent()).replace('Saved as ', '').trim()
  const md = existsSync(savedAs) ? readFileSync(savedAs, 'utf8') : ''
  check('Markdown has me and the named guests', md.includes('Me:**') && md.includes('Anna Test:**') && md.includes('Bo Test:**') && !md.includes('Guest'), savedAs)

  await page.goto(base + '/#/')
  await page.getByRole('heading', { name: 'Record a call' }).waitFor()
  const last = await page.evaluate(async () => (await (await fetch('/settings')).json()).last_sources.scope)
  check('the next recording is a normal call again', !(await page.getByLabel(/Interview mode/).isChecked()) && last !== 'none', `scope ${last}`)
} catch (e) {
  check('flow completed', false, e.message.split('\n')[0])
  await shot('m9-error').catch(() => {})
} finally {
  if (session) await fetch(`${base}/sessions/${session}`, { method: 'DELETE' }).catch(() => {})
  for (const p of players) p.kill()
  await browser.close()
  for (const m of modules.reverse()) {
    try { pactl('unload-module', m) } catch { /* already gone */ }
  }
}
const ok = results.every(Boolean)
console.log(`\nRESULT: ${ok ? 'PASS' : 'FAIL'}  (screenshots in ${shots})`)
process.exit(ok ? 0 : 1)
