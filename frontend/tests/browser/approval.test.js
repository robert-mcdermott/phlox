// Real Chromium + the real SPA, with isolated API fixtures (no model calls or user data).
import assert from 'node:assert/strict'
import { createServer as createHttpServer } from 'node:http'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { createServer } from 'vite'

let server, httpServer, browser, baseURL
before(async () => {
  server = await createServer({
    root: fileURLToPath(new URL('../..', import.meta.url)),
    server: { middlewareMode: true },
    logLevel: 'error',
  })
  httpServer = createHttpServer(server.middlewares)
  await new Promise((resolve) => httpServer.listen(0, '127.0.0.1', resolve))
  baseURL = `http://127.0.0.1:${httpServer.address().port}`
  browser = await chromium.launch({ headless: true })
})
after(async () => {
  await browser?.close()
  await server?.close()
  if (httpServer) await new Promise((resolve) => httpServer.close(resolve))
})

const sse = (...events) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
const pending = () => ({
  pending_id: 'approval-1', status: 'pending', calls: [
    { id: 'call-1', name: 'write_file', arguments: { path: 'plan.txt', content: 'Reviewed plan' } },
  ], content: 'I can save the plan.', tool_steps: [], artifacts: [],
  usage: { input: 7, output: 3, total: 10 }, expires_at: '2099-01-01T00:00:00Z',
})

async function fixture(t, { approval = null, auth = false } = {}) {
  const context = await browser.newContext()
  t.after(() => context.close())
  const page = await context.newPage()
  page.setDefaultTimeout(8000)
  const state = {
    approval, decisions: [], reject: false, authenticated: false, setup: true,
    messages: [{ id: 'user-1', role: 'user', content: 'Save the plan', created_at: '2026-09-07T00:00:00Z' }],
  }
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  t.after(() => assert.deepEqual(errors, [], 'no browser runtime errors'))
  await context.route('**/*', (route) => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort())
  await page.route(`${baseURL}/api/**`, async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    const json = (body, status = 200) => route.fulfill({ status, json: body })
    if (path === '/api/auth/config') return json({ enabled: auth })
    const user = { id: 'local', username: 'tester', role: 'admin', must_change_password: state.setup }
    if (path === '/api/auth/me') return state.authenticated ? json(user) : json({ detail: 'Sign in' }, 401)
    if (path === '/api/auth/login') {
      state.authenticated = true
      return json({ token: 'synthetic-test-token', user })
    }
    if (path === '/api/auth/change-password') { state.setup = false; return json({ ...user, must_change_password: false }) }
    if (path === '/api/conversations') return json([{ id: 'alpha', title: 'Approval chat' }, { id: 'beta', title: 'Other chat' }])
    if (path === '/api/conversations/alpha') return json({ id: 'alpha', title: 'Approval chat', messages: state.messages })
    if (path === '/api/conversations/beta') return json({ id: 'beta', title: 'Other chat', messages: [{ id: 'b', role: 'user', content: 'Only the other conversation' }] })
    if (path === '/api/chat/approvals/alpha') return json(state.approval ? [state.approval] : [])
    if (path === '/api/chat/approvals/beta') return json([])
    if (path === '/api/chat/approvals/approval-1' && method === 'DELETE') { state.approval = null; return json({ status: 'dismissed' }) }
    if (path === '/api/settings') return json({ active_profile: 'test', model: 'test-model', theme: 'phlox-dark', max_tokens: 1000, max_tool_rounds: 3 })
    if (path === '/api/providers') return json({ profiles: [{ name: 'test', label: 'Test', model: 'test-model' }] })
    if (path === '/api/settings/suggestions') return json({ suggestions: [] })
    if (path === '/api/usage/budget') return json({ budgets: [] })
    if (['/api/assistants', '/api/skills', '/api/documents'].includes(path)) return json([])
    if (path === '/api/chat') {
      state.approval = pending()
      return route.fulfill({ contentType: 'text/event-stream', body: sse(
        { type: 'conversation', id: 'alpha' },
        { type: 'token', content: 'I can save the plan.' },
        { type: 'approval_request', pending_id: 'approval-1', calls: state.approval.calls },
        { type: 'paused', pending_id: 'approval-1' },
      ) })
    }
    if (path === '/api/chat/approve') {
      if (state.reject) return json({ detail: 'Monthly budget exceeded' }, 402)
      const body = route.request().postDataJSON()
      state.decisions.push(body)
      state.approval = null
      state.messages.push({ id: 'answer', role: 'assistant', content: 'Decision handled once.' })
      return route.fulfill({ contentType: 'text/event-stream', body: sse(
        { type: 'conversation', id: 'alpha' }, { type: 'token', content: 'Decision handled once.' },
        { type: 'done', message_id: 'answer', outcome: 'completed' },
      ) })
    }
    throw new Error(`Unexpected API request: ${method} ${path}`)
  })
  await page.goto(baseURL, { waitUntil: 'domcontentloaded', timeout: 20000 })
  return { page, state }
}

async function openApproval(page) {
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText('Approval needed', { exact: true }).waitFor()
}

test('login and password setup lead to a streamed approval that survives reload', async (t) => {
  const { page, state } = await fixture(t, { auth: true })
  await page.getByLabel('Username', { exact: true }).fill('tester')
  await page.getByLabel('Password', { exact: true }).fill('temporary-test-password')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.getByLabel('Temporary password').fill('temporary-test-password')
  await page.getByLabel('New password', { exact: true }).fill('replacement-test-password')
  await page.getByLabel('Confirm new password').fill('replacement-test-password')
  await page.getByRole('button', { name: 'Set password and continue' }).click()
  await page.getByPlaceholder('Message Phlox…').fill('Save the plan')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByText('Approval needed', { exact: true }).waitFor()
  await page.reload()
  await openApproval(page)
  await page.getByText('plan.txt', { exact: false }).waitFor()
  await page.getByRole('button', { name: 'Approve & run' }).click()
  await page.getByText('Decision handled once.', { exact: true }).waitFor()
  assert.equal(await page.getByText('Approval needed', { exact: true }).count(), 0)
  assert.deepEqual(state.decisions, [{ pending_id: 'approval-1', decisions: { 'call-1': 'allow' } }])
})

test('a rejected approval remains visible and can be denied after the budget changes', async (t) => {
  const { page, state } = await fixture(t, { approval: pending() })
  await openApproval(page)
  state.reject = true
  await page.getByRole('button', { name: 'Approve & run' }).click()
  await page.getByText('Monthly budget exceeded', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Deny', exact: true }).waitFor()
  state.reject = false
  await page.getByRole('button', { name: 'Deny', exact: true }).click()
  await page.getByText('Decision handled once.', { exact: true }).waitFor()
  assert.equal(state.decisions[0].decisions['call-1'], 'deny')
})

test('expired approvals can be dismissed; unconfirmed claims cannot be replayed', async (t) => {
  const { page, state } = await fixture(t, { approval: { ...pending(), status: 'expired' } })
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText('This approval expired.', { exact: false }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Approve & run' }).count(), 0)
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await page.getByText('Approval status', { exact: true }).waitFor({ state: 'hidden' })
  state.approval = { ...pending(), status: 'claimed' }
  await page.getByText('Other chat', { exact: true }).click()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText('Execution has started', { exact: false }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Approve & run' }).count(), 0)
  assert.equal(await page.getByRole('button', { name: 'Dismiss', exact: true }).count(), 0)
  state.approval = null
  state.messages.push({ id: 'remote-finish', role: 'assistant', content: 'Completed in the other tab.' })
  await page.getByRole('button', { name: 'Refresh status', exact: true }).click()
  await page.getByText('Completed in the other tab.', { exact: true }).waitFor()
  assert.equal(await page.getByText('Approval status', { exact: true }).count(), 0)
})

test('Stop and chat switching reject late events even from a transport that ignores abort', async (t) => {
  const { page } = await fixture(t)
  // Simulate a badly behaved transport at the fetch boundary; exercise the real store
  // and SSE parser, including a late conversation event after a newer chat is selected.
  await page.evaluate(() => {
    const original = window.fetch
    window.streamControllers = []
    window.fetch = (url, options) => {
      if (url === '/api/chat') return Promise.resolve(new Response(new ReadableStream({
        start(controller) { window.streamControllers.push(controller) },
      }), { headers: { 'Content-Type': 'text/event-stream' } }))
      return original(url, options)
    }
  })
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByPlaceholder('Message Phlox…').fill('hold this stream')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await page.getByText('Other chat', { exact: true }).click()
  await page.getByText('Only the other conversation', { exact: true }).waitFor()
  await page.getByPlaceholder('Message Phlox…').fill('a newer stream in the other chat')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByRole('button', { name: 'Stop', exact: true }).waitFor()
  await page.evaluate(() => {
    window.streamControllers[0].enqueue(new TextEncoder().encode(
      'data: {"type":"conversation","id":"alpha"}\n\ndata: {"type":"token","content":"STALE RESPONSE"}\n\n',
    ))
    window.streamControllers[0].close()
  })
  // Drain the actual asynchronous parser and finalization callback before asserting.
  await page.evaluate(async () => {
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0))
  })
  assert.equal(await page.getByText('STALE RESPONSE', { exact: true }).count(), 0)
  assert.equal(await page.getByText('Only the other conversation', { exact: true }).count(), 1)
  assert.equal(await page.getByRole('button', { name: 'Stop', exact: true }).count(), 1)
  await page.evaluate(() => window.streamControllers[1].enqueue(new TextEncoder().encode(
    'data: {"type":"token","content":"CURRENT RESPONSE"}\n\n',
  )))
  await page.getByText('CURRENT RESPONSE', { exact: true }).waitFor()
  assert.match(await page.getByText('Other chat', { exact: true }).locator('..').getAttribute('class'), /bg-white\/15/)
  await page.evaluate(() => window.streamControllers[1].close())
})
