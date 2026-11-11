// M5 check: the full flow in the browser UI, no terminal.
//
// Needs the service running (aku serve) and ../scripts/make_test_audio.py run once.
// Uses silent virtual devices: nothing plays on real speakers, no real mic is used.
//
// Run:  cd frontend && node e2e/check_ui.mjs [http://127.0.0.1:4587]

import { execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromium } from 'playwright'

const base = process.argv[2] ?? 'http://127.0.0.1:4587'
const audio = join(homedir(), '.cache', 'aku-test')
const shots = join(process.cwd(), '..', 'tmp', 'shots')
mkdirSync(shots, { recursive: true })
const outDir = mkdtempSync(join(tmpdir(), 'aku-ui-'))

const pactl = (...args) => execFileSync('pactl', args, { encoding: 'utf8' }).trim()
const modules = [
  pactl('load-module', 'module-null-sink', 'sink_name=aku_check_out', 'sink_properties=device.description=AkuCheckOut'),
  pactl('load-module', 'module-null-sink', 'sink_name=aku_check_mic', 'sink_properties=device.description=AkuCheckMicFeed'),
  pactl('load-module', 'module-remap-source', 'master=aku_check_mic.monitor', 'source_name=aku_check_src', 'source_properties=device.description=AkuCheckMic'),
]
const players = []
const results = []
const check = (name, ok, extra = '') => {
  results.push(ok)
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${name}${extra ? ' - ' + extra : ''}`)
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1180, height: 900 } })
const shot = (name) => page.screenshot({ path: join(shots, `${name}.png`), fullPage: true })
page.on('pageerror', (e) => check('no page errors', false, e.message))

try {
  await page.goto(base + '/')
  await page.getByRole('heading', { name: 'Record a call' }).waitFor()
  await page.waitForFunction(() => [...document.querySelectorAll('option')].some((o) => o.textContent.includes('AkuCheckMic')))
  const selects = page.locator('.panel select')
  await selects.nth(0).selectOption({ label: 'AkuCheckMic' })
  await page.getByLabel('Everything playing on an output device').check()
  await selects.nth(1).selectOption({ label: 'AkuCheckOut' })
  await shot('1-record')

  players.push(spawn('pw-play', ['--target', 'aku_check_out', join(audio, 'call_system.wav')], { stdio: 'ignore' }))
  players.push(spawn('pw-play', ['--target', 'aku_check_mic', join(audio, 'call_mic.wav')], { stdio: 'ignore' }))
  await page.getByRole('button', { name: /Start recording/ }).click()
  await page.getByRole('heading', { name: /Recording/ }).waitFor()
  check('recording starts from the UI', true)

  await page.waitForFunction(() => document.querySelectorAll('.line').length >= 6, null, { timeout: 90_000 })
  const names = await page.locator('.line .who').allTextContents()
  check('live transcript shows both sides', names.some((n) => n.startsWith('Them')) && names.some((n) => !n.startsWith('Them')), names.slice(0, 6).join(' '))
  await shot('2-live')

  await page.locator('select').last().selectOption('3')
  await page.getByRole('button', { name: /Stop/ }).click()
  await page.getByRole('heading', { name: 'Who was on the call?' }).waitFor({ timeout: 120_000 })
  const inputs = page.locator('input[aria-label^="Name for"]')
  await inputs.first().waitFor()
  const count = await inputs.count()
  check('speakers separated', count >= 2, `${count} speakers`)

  const typed = ['Anna Test', 'Bo Test', 'Cy Test']
  for (let i = 0; i < count; i++) {
    await inputs.nth(i).fill(typed[i] ?? `Person ${i + 1}`)
    await inputs.nth(i).blur()
  }
  await page.waitForFunction(() => document.querySelector('.transcript')?.textContent.includes('Anna Test'))
  check('names appear in the transcript', true)
  await page.getByLabel('Remember this voice').first().check()

  const clip = await page.request.get(base + (await page.evaluate(() => location.hash.replace('#', ''))) + '/speakers/S1/clip')
  check('speaker clip is playable audio', clip.ok() && (await clip.body()).length > 20_000, clip.headers()['content-type'])

  // fix from transcript: double-click a word, type the correction
  const firstText = page.locator('.line .text').first()
  const before = (await firstText.textContent()).trim()
  const word = before.split(/\s+/).find((w) => /^[A-Za-z]{5,}$/.test(w))
  const box = await firstText.boundingBox()
  await page.evaluate((w) => {
    const node = document.querySelector('.line .text').firstChild
    const i = node.textContent.indexOf(w)
    const range = document.createRange()
    range.setStart(node, i)
    range.setEnd(node, i + w.length)
    const sel = getSelection()
    sel.removeAllRanges()
    sel.addRange(range)
  }, word)
  await page.mouse.move(box.x + 5, box.y + 5)
  await page.locator('.line .text').first().dispatchEvent('mouseup')
  await page.locator('.popover input').fill('Akufix')
  await shot('3-fix')
  await page.getByRole('button', { name: 'Fix here and in future' }).click()
  await page.waitForFunction(() => document.querySelector('.line .text')?.textContent.includes('Akufix'))
  check('fix-from-transcript rewrites the word', true, `${word} -> Akufix`)

  await page.getByLabel('Title').fill('UI check call')
  await page.getByLabel('Folder').fill(outDir)
  await shot('4-naming')
  await page.getByRole('button', { name: 'Finalise and save Markdown' }).click()
  await page.getByText(/Saved as/).waitFor()
  const savedAs = (await page.getByText(/Saved as/).textContent()).replace('Saved as ', '').trim()
  const md = existsSync(savedAs) ? readFileSync(savedAs, 'utf8') : ''
  check('Markdown written to the chosen folder', savedAs.startsWith(outDir) && md.includes('Anna Test:') && md.includes('Akufix'), savedAs)
  await shot('5-finalised')

  await page.goto(base + '/#/sessions')
  await page.locator('a.panel', { hasText: 'UI check call' }).first().waitFor()
  await shot('6-sessions')

  await page.goto(base + '/#/settings')
  await page.getByRole('heading', { name: 'Saved voices' }).waitFor()
  await page.getByText('Anna Test').waitFor()
  check('remembered voice is listed in Settings', true)
  await shot('7-settings')
  await page.getByRole('button', { name: 'Forget' }).first().click()
  await page.getByText('No voices saved.').waitFor()
  check('Forget removes the voice', true)

  // delete audio (behind a warning), then the session
  await page.goto(base + '/#/sessions')
  await page.locator('a.panel', { hasText: 'UI check call' }).first().click()
  await page.getByRole('button', { name: 'Delete audio, keep transcript' }).click()
  await page.getByRole('dialog').getByText(/can no longer improve/).waitFor()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete audio' }).click()
  await page.getByText('The audio was deleted.').waitFor()
  check('audio deleted behind a warning, transcript kept', (await page.locator('.line').count()) > 0)
  await page.getByRole('button', { name: 'Delete this session' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete session' }).click()
  await page.getByRole('heading', { name: 'Transcripts' }).waitFor()
  await page.waitForTimeout(500)
  check('session deleted', !(await page.locator('a.panel', { hasText: 'UI check call' }).count()))
} catch (e) {
  check('flow completed', false, e.message.split('\n')[0])
  await shot('error').catch(() => {})
} finally {
  for (const p of players) p.kill()
  await browser.close()
  for (const m of modules.reverse()) {
    try { pactl('unload-module', m) } catch { /* already gone */ }
  }
}
const ok = results.every(Boolean)
console.log(`\nRESULT: ${ok ? 'PASS' : 'FAIL'}  (screenshots in ${shots})`)
process.exit(ok ? 0 : 1)
