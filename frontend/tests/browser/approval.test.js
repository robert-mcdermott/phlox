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

async function fixture(t, { approval = null, auth = false, durable = false } = {}) {
  const context = await browser.newContext()
  t.after(() => context.close())
  const page = await context.newPage()
  page.setDefaultTimeout(8000)
  const state = {
    sources: {}, sourceReads: [], exportReads: 0, exportMarkdown: '',
    approval, decisions: [], reject: false, authenticated: false, setup: true,
    run: null, events: [], cursors: [], cancellations: 0, creates: [], loseAcceptance: false,
    config: { providers: [], pricing: {}, resilience: {}, generation: {}, suggestions: [],
      sandbox: { runner: 'local', container: {} } },
    messages: [{ id: 'user-1', role: 'user', content: 'Save the plan', created_at: '2026-09-07T00:00:00Z' }],
  }
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (msg) => {
    if (msg.type() === 'error' && !msg.text().includes('Failed to load resource')) errors.push(msg.text())
  })
  t.after(() => assert.deepEqual(errors, [], 'no browser runtime errors'))
  await context.route('**/*', (route) => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort())
  await context.route(`${baseURL}/api/**`, async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    const json = (body, status = 200) => route.fulfill({ status, json: body })
    if (path === '/api/auth/config') return json({ enabled: auth, runs_enabled: durable })
    const user = { id: 'local', username: 'tester', role: 'admin', must_change_password: state.setup }
    if (path === '/api/auth/me') return state.authenticated ? json(user) : json({ detail: 'Sign in' }, 401)
    if (path === '/api/auth/login') {
      state.authenticated = true
      return json({ token: 'synthetic-test-token', user })
    }
    if (path === '/api/auth/change-password') { state.setup = false; return json({ ...user, must_change_password: false }) }
    if (path === '/api/conversations') return json([{ id: 'alpha', title: 'Approval chat' }, { id: 'beta', title: 'Other chat' }])
    if (path.startsWith('/api/conversations/alpha/sources/')) {
      const id = path.split('/').at(-1)
      state.sourceReads.push(id)
      return state.sources[id] ? json(state.sources[id]) : json({ detail: 'Source not found' }, 404)
    }
    if (path === '/api/conversations/alpha/export') {
      state.exportReads++
      return json({ markdown: state.exportMarkdown })
    }
    if (path === '/api/conversations/alpha') return json({ id: 'alpha', title: 'Approval chat', messages: state.messages })
    if (path === '/api/conversations/beta') return json({ id: 'beta', title: 'Other chat', messages: [{ id: 'b', role: 'user', content: 'Only the other conversation' }] })
    if (path === '/api/chat/approvals/alpha') return json(state.approval ? [state.approval] : [])
    if (path === '/api/chat/approvals/beta') return json([])
    if (path === '/api/chat/approvals/approval-1' && method === 'DELETE') { state.approval = null; return json({ status: 'dismissed' }) }
    if (path === '/api/settings') return json({ active_profile: 'test', model: 'test-model', theme: 'phlox-dark', max_tokens: 1000, max_tool_rounds: 3 })
    if (path === '/api/providers') return json({ profiles: [{ name: 'test', label: 'Test', model: 'test-model' }] })
    if (path === '/api/providers/test/models') return json({ profile: 'test', models: ['test-model'] })
    if (path === '/api/settings/suggestions') return json({ suggestions: [] })
    if (path === '/api/usage/budget') return json({ budgets: [] })
    if (path === '/api/admin/config') return json(state.config)
    if (path === '/api/admin/config/pricing' && method === 'PUT') {
      state.config.pricing = route.request().postDataJSON().pricing
      return json(state.config)
    }
    if (path === '/api/usage/by-user') return json({ rows: [{
      month: '2026-09', department: 'Research', username: 'tester', email: '', user_id: 'local',
      model: 'test-model', input_tokens: 7, output_tokens: 3, total_tokens: 10,
      cost_usd: null, known_cost_usd: 0.5, unknown_usage_calls: 1, unknown_cost_calls: 1,
      calls: 2, turns: 1,
    }] })
    if (['/api/assistants', '/api/skills', '/api/documents'].includes(path)) return json([])
    if (path === '/api/runs' && method === 'POST') {
      const key = route.request().headers()['idempotency-key']
      state.creates.push(key)
      if (!state.run) {
        state.run = { id: 'run-1', conversation_id: 'alpha', status: 'running' }
        state.events = [{ type: 'conversation', id: 'alpha' }, { type: 'token', content: 'Saved progress.' }]
      }
      if (state.loseAcceptance) { state.loseAcceptance = false; return route.abort('failed') }
      return json(state.run)
    }
    if (path === '/api/runs' && method === 'GET') {
      return json(new URL(route.request().url()).searchParams.get('conversation_id') === 'alpha' ? state.run : null)
    }
    if (path === '/api/runs/run-1/events') {
      const cursor = Number(new URL(route.request().url()).searchParams.get('after'))
      state.cursors.push(cursor)
      const events = state.events.map((event, index) => ({ ...event, seq: index + 1, run_id: 'run-1' }))
        .filter((event) => event.seq >= Math.max(1, cursor)) // Deliberate duplicate exercises client deduplication.
      return route.fulfill({ contentType: 'text/event-stream', body: sse(...events, { type: 'run_state', ...state.run }) })
    }
    if (path === '/api/runs/run-1/cancel') {
      state.cancellations++
      state.run.status = 'cancel_requested'
      return json(state.run)
    }
    if (path === '/api/runs/run-1/approve') {
      if (state.reject) return json({ detail: 'Monthly budget exceeded' }, 402)
      if (state.run.status !== 'awaiting_approval') return json({ detail: 'Already claimed' }, 409)
      state.decisions.push(route.request().postDataJSON())
      state.approval = null
      state.run.status = 'completed'
      state.messages.push({ id: 'answer', role: 'assistant', content: 'Decision handled once.' })
      state.events.push({ type: 'token', content: 'Decision handled once.' }, { type: 'done', message_id: 'answer', outcome: 'completed' })
      return json(state.run)
    }
    if (path === '/api/runs/run-1/acknowledge') {
      state.run.needs_acknowledgement = false
      return json(state.run)
    }
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
  return { page, state, context }
}

async function openApproval(page) {
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText('Approval needed', { exact: true }).waitFor()
}

test('partial call usage stays visibly unknown in message receipts and chargeback', async (t) => {
  const { page, state } = await fixture(t)
  state.messages.push({ id: 'partial', role: 'assistant', content: 'Partial result.', usage: {
    accounting: 'model_calls', input: 7, output: 3, total: 10, cost: null,
    known_cost: 0.5, unknown_usage_calls: 1, unknown_cost_calls: 1,
  } })
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText(/10 tok \(partial \/ unknown\).*cost unknown/).waitFor()
  await page.getByTitle('Settings', { exact: true }).click()
  await page.getByRole('button', { name: 'Usage & Cost', exact: true }).click()
  await page.getByText('$0.50 + unknown', { exact: true }).first().waitFor()
  await page.getByText('Calls / legacy entries', { exact: true }).waitFor()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export CSV' }).click()
  const download = await downloadPromise
  const stream = await download.createReadStream()
  const chunks = []
  for await (const chunk of stream) chunks.push(chunk)
  const csv = Buffer.concat(chunks).toString()
  assert.match(csv, /cost_usd,known_cost_usd,unknown_usage_calls,unknown_cost_calls,calls,turns/)
  assert.match(csv, /7,3,10,,0.5,1,1,2,1/)
})

test('pricing keeps blank rates unknown and saves explicit zero and cache rates', async (t) => {
  const { page, state } = await fixture(t)
  await page.getByTitle('Settings', { exact: true }).click()
  await page.getByRole('button', { name: 'Configuration', exact: true }).click()
  await page.getByRole('button', { name: 'Add model', exact: true }).click()
  await page.getByPlaceholder('model id', { exact: true }).fill('local-model')
  const pricing = page.getByRole('heading', { name: 'Model pricing', exact: true }).locator('../..')
  await pricing.getByLabel('Input $/1M', { exact: true }).fill('0')
  await pricing.getByLabel('Cached input $/1M', { exact: true }).fill('0.1')
  await page.getByRole('button', { name: 'Save pricing', exact: true }).click()
  await pricing.getByText('Saved — applied live.', { exact: true }).waitFor()
  assert.deepEqual(state.config.pricing['local-model'], {
    input: 0, output: null, cache_read: 0.1, cache_write: null,
  })
  assert.equal(await pricing.getByLabel('Output $/1M', { exact: true }).inputValue(), '')
})

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


test('durable runs retry acceptance once, deduplicate replay, detach on navigation and confirm Stop', async (t) => {
  const { page, state } = await fixture(t, { durable: true })
  state.loseAcceptance = true
  await page.getByPlaceholder('Message Phlox…').fill('Keep working')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByText('Saved progress.', { exact: true }).waitFor()
  assert.equal(state.creates.length, 2)
  assert.equal(state.creates[0], state.creates[1])
  await page.waitForFunction(async () => {
    const { useStore } = await import('/src/store/useStore.js')
    return useStore.getState().connection?.includes('Reconnecting')
  })
  await page.getByText('Other chat', { exact: true }).click()
  await page.getByText('Only the other conversation', { exact: true }).waitFor()
  assert.equal(state.cancellations, 0)
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText('Saved progress.', { exact: true }).waitFor()
  await page.reload()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText('Saved progress.', { exact: true }).waitFor()
  await page.waitForFunction(async () => {
    const { useStore } = await import('/src/store/useStore.js')
    return useStore.getState().connection?.includes('Reconnecting')
  })
  assert.equal(await page.getByText('Saved progress.', { exact: true }).count(), 1)
  await page.getByTitle('Stop', { exact: true }).click()
  await page.getByText('Stopping — awaiting confirmation', { exact: true }).waitFor()
  assert.equal(state.cancellations, 1)
  state.run.status = 'cancelled'
  await page.getByText('Stopped', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Send', exact: true }).waitFor()
  assert.ok(state.cursors.some((cursor) => cursor > 0))
})

test('durable approval survives reload and a second tab refresh sees the single completion', async (t) => {
  const { page, state, context } = await fixture(t, { durable: true, approval: pending() })
  state.run = { id: 'run-1', conversation_id: 'alpha', status: 'awaiting_approval', pending_id: 'approval-1' }
  state.events = [{ type: 'token', content: 'I can save the plan.' }]
  await openApproval(page)
  await page.reload()
  await openApproval(page)
  const other = await context.newPage()
  await other.goto(baseURL)
  await openApproval(other)
  state.reject = true
  await page.getByRole('button', { name: 'Approve & run', exact: true }).click()
  await page.getByText('Monthly budget exceeded', { exact: true }).waitFor()
  await page.getByText('Approval needed', { exact: true }).waitFor()
  state.reject = false
  await page.getByRole('button', { name: 'Approve & run', exact: true }).click()
  await page.getByText('Decision handled once.', { exact: true }).waitFor()
  assert.equal(state.decisions.length, 1)
  await other.reload()
  await other.getByText('Approval chat', { exact: true }).click()
  await other.getByText('Decision handled once.', { exact: true }).waitFor()
  assert.equal(state.decisions.length, 1)
})

test('interrupted progress stays visible until acknowledged and logout detaches private state', async (t) => {
  const { page, state } = await fixture(t, { durable: true, auth: true })
  state.setup = false
  state.run = { id: 'run-1', conversation_id: 'alpha', status: 'interrupted', needs_acknowledgement: true, reason: 'Action outcome unknown.' }
  state.events = [{ type: 'token', content: 'Saved progress.' }]
  const login = async () => {
    await page.getByLabel('Username', { exact: true }).fill('tester')
    await page.getByLabel('Password', { exact: true }).fill('test-password')
    await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  }
  await login()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText('Saved progress.', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'I reviewed the results — allow a new turn', exact: true }).click()
  await page.getByRole('button', { name: 'I reviewed the results — allow a new turn', exact: true }).waitFor({ state: 'hidden' })
  state.run.status = 'running'
  state.events = [{ type: 'token', content: 'Still running.' }]
  await page.getByText('Other chat', { exact: true }).click()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText('Still running.', { exact: true }).waitFor()
  await page.evaluate(async () => { const { useStore } = await import('/src/store/useStore.js'); useStore.getState().logout() })
  await page.getByRole('button', { name: 'Sign in', exact: true }).waitFor()
  const privateState = await page.evaluate(async () => { const { useStore } = await import('/src/store/useStore.js'); const s = useStore.getState(); return [s.run, s.live, s.messages.length, s.conversations.length] })
  assert.deepEqual(privateState, [null, null, 0, 0])
  assert.equal(state.cancellations, 0)
  await login()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText('Still running.', { exact: true }).waitFor()
})

const sourceRef = { label: 'S1', source_id: 'source-1' }
const sourceFixture = () => ({
  id: 'source-1', label: 'S1', available: true, title: 'Evidence.txt',
  excerpt: 'Retain the original evidence. <script>untrusted()</script>',
  location: { chunk: 2, start: 0, end: 63, truncated: true },
  captured_at: '2026-09-07T00:00:00Z', changed: true,
})

test('saved citations inspect exact evidence, preserve code, reload and recheck revoked access', async (t) => {
  const { page, state } = await fixture(t)
  state.sources['source-1'] = sourceFixture()
  state.messages.push({ id: 'cited', role: 'assistant',
    content: 'Evidence [S1]. Unknown [S99]. Inline `[S1]`.\n\n```text\n[S1]\n```',
    citations: [sourceRef, { label: 'S99', source_id: null }],
  })
  await page.getByText('Approval chat', { exact: true }).click()
  const chip = page.getByRole('button', { name: 'View source S1', exact: true })
  await chip.waitFor()
  assert.equal(await chip.count(), 1)
  assert.equal(await page.getByRole('button', { name: 'Unverified source S99' }).isDisabled(), true)
  assert.equal(await page.locator('code').filter({ hasText: '[S1]' }).count(), 2)
  await chip.click()
  await page.getByRole('dialog').getByText(state.sources['source-1'].excerpt, { exact: true }).waitFor()
  await page.getByText('Chunk 3 · Characters 1–63', { exact: true }).waitFor()
  await page.getByText('The document has changed since this excerpt was captured.', { exact: true }).waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.equal(await chip.evaluate((el) => el === document.activeElement), true)
  await page.reload()
  await page.getByText('Approval chat', { exact: true }).click()
  state.sources['source-1'] = { id: 'source-1', label: 'S1', available: false, reason: 'Source unavailable after deletion.' }
  await chip.click()
  await page.getByText('Source unavailable after deletion.', { exact: true }).waitFor()
  assert.equal(await page.getByText('Evidence.txt', { exact: true }).count(), 0)
  assert.equal(await page.getByRole('dialog').locator('blockquote').count(), 0)
  assert.ok(state.sourceReads.length >= 2 && state.sourceReads.every((id) => id === 'source-1'))
  await page.getByRole('button', { name: 'Close source' }).click()
  state.exportMarkdown = '# Approval chat\n\nEvidence [S1].\n\n## Sources\n\n[S1] Source unavailable.'
  const downloaded = page.waitForEvent('download')
  await page.evaluate(async () => { const { useStore } = await import('/src/store/useStore.js'); await useStore.getState().exportConversation('alpha') })
  const stream = await (await downloaded).createReadStream()
  const chunks = []
  for await (const chunk of stream) chunks.push(chunk)
  assert.equal(Buffer.concat(chunks).toString(), state.exportMarkdown)
  assert.equal(state.exportReads, 1)
})

test('approval snapshots and durable event replay keep citation identity in new tabs', async (t) => {
  const approval = { ...pending(), content: 'Evidence [S1].', sources: [sourceRef] }
  const { page, state, context } = await fixture(t, { durable: true, approval })
  state.sources['source-1'] = sourceFixture()
  state.run = { id: 'run-1', conversation_id: 'alpha', status: 'awaiting_approval', pending_id: 'approval-1' }
  state.events = [{ type: 'sources', sources: [sourceRef] }, { type: 'token', content: 'Evidence [S1].' }]
  await openApproval(page)
  await page.getByRole('button', { name: 'View source S1', exact: true }).click()
  await page.getByRole('dialog').getByText('Evidence.txt', { exact: true }).waitFor()
  await page.keyboard.press('Escape')
  await page.reload()
  await openApproval(page)
  await page.getByRole('button', { name: 'View source S1', exact: true }).waitFor()
  const other = await context.newPage()
  await other.goto(baseURL)
  await openApproval(other)
  await other.getByRole('button', { name: 'View source S1', exact: true }).click()
  await other.getByRole('dialog').getByText('Evidence.txt', { exact: true }).waitFor()
  assert.ok(state.sourceReads.length >= 2 && state.sourceReads.every((id) => id === 'source-1'))
  await other.close()
  // A running subscription rebuilds the same catalog from persisted events alone.
  state.approval = null
  state.run.status = 'running'
  await page.reload()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByRole('button', { name: 'View source S1', exact: true }).click()
  await page.getByRole('dialog').getByText('Evidence.txt', { exact: true }).waitFor()
})
