// Check the language selector in the browser: choose Automatic, record Danish speech, see Danish text.
//
// Needs a running service and ../scripts/make_test_audio.py run once. Uses a silent virtual device.
// It deletes its own session and puts the language setting back.
//
// Run:  cd frontend && node e2e/check_language.mjs [http://127.0.0.1:4587]

import { execFileSync, spawn } from 'node:child_process'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { chromium } from 'playwright'

const base = process.argv[2] ?? 'http://127.0.0.1:4587'
const pactl = (...args) => execFileSync('pactl', args, { encoding: 'utf8' }).trim()
const mod = pactl('load-module', 'module-null-sink', 'sink_name=aku_check_out', 'sink_properties=device.description=AkuCheckOut')
const results = []
const check = (name, ok, extra = '') => {
  results.push(ok)
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${name}${extra ? ' - ' + extra : ''}`)
}
const before = await (await fetch(base + '/settings')).json()
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1180, height: 900 } })
let player
let sid

try {
  await page.goto(base + '/')
  await page.getByRole('heading', { name: 'Record a call' }).waitFor()
  const language = page.getByLabel('Language spoken')
  await language.waitFor()
  const options = await language.locator('option').allTextContents()
  check('selector offers Automatic, English and Danish', ['Automatic (detect)', 'English', 'Danish'].every((o) => options.map((t) => t.trim()).includes(o)), `${options.length} choices`)
  await language.selectOption('auto')
  await page.waitForFunction(() => [...document.querySelectorAll('option')].some((o) => o.textContent.includes('AkuCheckOut')))
  await page.getByLabel('Everything playing on an output device').check()
  await page.locator('.panel select').nth(1).selectOption({ label: 'AkuCheckOut' })

  player = spawn('pw-play', ['--target', 'aku_check_out', join(homedir(), '.cache', 'aku-test', 'danish.wav')], { stdio: 'ignore' })
  await page.getByRole('button', { name: /Start recording/ }).click()
  await page.getByRole('heading', { name: /Recording/ }).waitFor()
  sid = (await (await fetch(base + '/status')).json()).session
  await page.waitForFunction(() => document.querySelectorAll('.line').length >= 2, null, { timeout: 90_000 })
  const text = (await page.locator('.line .text').allTextContents()).join(' ')
  check('Danish speech comes out in Danish with Automatic', /[æøå]/i.test(text) && /\b(og|det|ikke|som|til)\b/i.test(text), text.slice(0, 90))

  await page.getByRole('button', { name: /Stop/ }).click()
  await page.getByRole('heading', { name: 'Who was on the call?' }).waitFor({ timeout: 120_000 })
  check('the transcript page shows the language choice', (await page.locator('.page p.muted').first().textContent()).includes('Automatic'))
  const saved = await (await fetch(base + '/settings')).json()
  check('the choice is remembered for the next recording', saved.language === 'auto')
} catch (e) {
  check('flow completed', false, e.message.split('\n')[0])
} finally {
  player?.kill()
  await browser.close()
  const put = (body) => fetch(base + '/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  await put({ language: before.language, last_sources: before.last_sources }).catch(() => {})
  if (sid) {
    for (let i = 0; i < 60; i++) {
      const r = await fetch(`${base}/sessions/${sid}`, { method: 'DELETE' }).catch(() => null)
      if (r?.ok) break
      await new Promise((done) => setTimeout(done, 1000))
    }
  }
  try { pactl('unload-module', mod) } catch { /* already gone */ }
}
const ok = results.every(Boolean)
console.log(`\nRESULT: ${ok ? 'PASS' : 'FAIL'}`)
process.exit(ok ? 0 : 1)
