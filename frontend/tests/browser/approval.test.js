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
    savedFileReads: [],
    branchMode: false, branchViews: {}, selections: [], branchFailure: false, activeLeaf: null,
    projects: [], projectSaves: [], conversationProjects: {}, contextReads: [],
    settings: { active_profile: 'test', model: 'test-model', theme: 'phlox-dark', max_tokens: 1000, max_tool_rounds: 3 },
    modelCatalog: { profile: 'test', models: ['test-model'] }, modelReads: 0,
    discoveryRequests: [], profileSaves: [],
    searchSaves: [], searchTests: [], chatRequests: [], docs: [], retries: [], rebuilds: 0, index: { mode: 'keyword', rebuild_required: true, notice: 'Embedding model changed. Rebuild required.' },
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
    if (path === '/api/files/alpha/saved/answer-old/0') {
      state.savedFileReads.push(path)
      return route.fulfill({ contentType: 'text/plain', body: '# Original retained report\n\nSaved bytes from the original answer.' })
    }
    if (path === '/api/auth/config') return json({ enabled: auth, runs_enabled: durable })
    const user = { id: 'local', username: 'tester', role: 'admin', must_change_password: state.setup }
    if (path === '/api/auth/me') return state.authenticated ? json(user) : json({ detail: 'Sign in' }, 401)
    if (path === '/api/auth/login') {
      state.authenticated = true
      return json({ token: 'synthetic-test-token', user })
    }
    if (path === '/api/auth/change-password') { state.setup = false; return json({ ...user, must_change_password: false }) }
    if (path === '/api/conversations') return json([{ id: 'alpha', title: 'Approval chat', project_id: state.conversationProjects.alpha }, { id: 'beta', title: 'Other chat', project_id: state.conversationProjects.beta }])
    if (path === '/api/conversations/alpha/context/turn-project') {
      state.contextReads.push('turn-project')
      return json({ project_name: 'Infrastructure', profile: 'test', model: 'test-model', history_messages: 0,
        base_instructions: 'Be helpful.', instructions: 'Prefer repairable equipment.', memories: [],
        calls: [{ profile: 'test', model: 'test-model', kind: 'chat', source_ids: ['source-project'], project_instructions_present: true }],
        sources: [{ id: 'source-project', label: 'S1', title: 'Network notes', available: true, excerpt: 'Retain local backups for 30 days.' }] })
    }
    if (path.startsWith('/api/conversations/alpha/sources/')) {
      const id = path.split('/').at(-1)
      state.sourceReads.push(id)
      if (method === 'DELETE') {
        state.sources[id] = { id, available: false, reason: 'Retained web snapshot removed.' }
        return json({ deleted: id })
      }
      return state.sources[id] ? json(state.sources[id]) : json({ detail: 'Source not found' }, 404)
    }
    if (path === '/api/conversations/alpha/export') {
      state.exportReads++
      return json({ markdown: state.exportMarkdown })
    }
    if (path.startsWith('/api/conversations/alpha/alternatives/')) {
      const target = path.split('/').at(-1)
      state.selections.push(route.request().postDataJSON())
      state.messages = state.branchViews[target]
      state.activeLeaf = state.messages.at(-1).id
      return json({ id: 'alpha', title: 'Approval chat', messages: state.messages, active_leaf_id: state.activeLeaf, has_alternatives: true })
    }
    if (path === '/api/conversations/alpha') {
      if (method === 'PATCH') state.conversationProjects.alpha = route.request().postDataJSON().project_id
      return json({ id: 'alpha', title: 'Approval chat', messages: state.messages, active_leaf_id: state.activeLeaf, has_alternatives: state.branchMode, project_id: state.conversationProjects.alpha })
    }
    if (path === '/api/conversations/beta') return json({ id: 'beta', title: 'Other chat', messages: [{ id: 'b', role: 'user', content: 'Only the other conversation' }] })
    if (path === '/api/chat/approvals/alpha') return json(state.approval ? [state.approval] : [])
    if (path === '/api/chat/approvals/beta') return json([])
    if (path === '/api/chat/approvals/approval-1' && method === 'DELETE') { state.approval = null; return json({ status: 'dismissed' }) }
    if (path === '/api/settings') {
      if (method === 'PATCH') Object.assign(state.settings, route.request().postDataJSON())
      return json(state.settings)
    }
    if (path === '/api/projects') {
      if (method === 'POST') {
        const row = { ...route.request().postDataJSON(), id: 'project-1', revision: 1 }
        state.projects.push(row); state.projectSaves.push(row)
        return json(row)
      }
      return json(state.projects)
    }
    if (path.startsWith('/api/projects/')) {
      const id = path.split('/').at(-1)
      const index = state.projects.findIndex(p => p.id === id)
      if (method === 'PUT') {
        state.projects[index] = { ...route.request().postDataJSON(), id, revision: state.projects[index].revision + 1 }
        state.projectSaves.push(state.projects[index])
      }
      return json({ ...state.projects[index], conversations: Object.keys(state.conversationProjects).filter(c => state.conversationProjects[c] === id).map(c => ({ id: c, title: 'Approval chat' })) })
    }
    if (path === '/api/context/preview') {
      const body = route.request().postDataJSON()
      const p = state.projects.find(p => p.id === (state.conversationProjects[body.conversation_id] || body.project_id))
      return json({ project_id: p?.id, project_name: p?.name, profile: 'test', model: 'test-model', destination: 'localhost', base_instructions: 'Be helpful.',
        instructions: body.context.project_instructions === false ? '' : p?.instructions, history_messages: 0,
        memory_enabled: body.context.memory ?? !p,
        memories: body.context.memory ? [{ id: 'memory-1', content: 'Personal preference', selected: true }] : [],
        documents: state.docs.filter(d => p?.document_ids.includes(d.id)).map(d => ({ ...d, selected: !(body.context.excluded_document_ids || []).includes(d.id) })) })
    }
    if (path === '/api/providers') return json({ profiles: [{ name: 'test', label: 'Test', model: 'test-model' }] })
    if (path === '/api/providers/test/models') { state.modelReads++; return json(state.modelCatalog) }
    if (path === '/api/settings/suggestions') return json({ suggestions: [] })
    if (path === '/api/usage/budget') return json({ budgets: [] })
    if (path === '/api/admin/config') return json(state.config)
    if (path === '/api/admin/config/profiles/discover') {
      state.discoveryRequests.push(route.request().postDataJSON())
      return json(state.modelCatalog)
    }
    if (path === '/api/admin/config/profiles' && method === 'PUT') {
      const body = route.request().postDataJSON(); state.profileSaves.push(body)
      state.config.providers = body.profiles.map(({ api_key, ...p }) => ({ ...p, api_key_set: !!api_key }))
      return json(state.config)
    }
    if (path === '/api/admin/config/web-search' && method === 'PUT') {
      const body = route.request().postDataJSON(); state.searchSaves.push(body)
      state.config.web_search = { engine: body.engine, interval_seconds: body.interval_seconds, searxng_url: body.searxng_url, serper_api_key_set: !!body.serper_api_key }
      return json(state.config)
    }
    if (path === '/api/admin/config/web-search/test') {
      state.searchTests.push(route.request().postDataJSON())
      return json({ ok: true, engine: 'ddg', result_count: 3, fallback_reason: 'Primary HTTP 403.' })
    }
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
    if (path === '/api/documents/index-status') return json(state.index)
    if (path === '/api/documents/reindex') {
      state.rebuilds++
      state.index = { ...state.index, status: 'queued' }
      return json(state.index)
    }
    if (path === '/api/documents/doc-1/retry') {
      state.retries.push('doc-1')
      state.docs[0] = { ...state.docs[0], status: 'queued', error: null, ingestion: { stage: 'queued' } }
      return json(state.docs[0])
    }
    if (path === '/api/documents') return json(state.docs)
    if (['/api/assistants', '/api/skills'].includes(path)) return json([])
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
      state.chatRequests.push(route.request().postDataJSON())
      if (state.branchMode) {
        const body = route.request().postDataJSON()
        if (state.branchFailure) return route.fulfill({ contentType: 'text/event-stream', body: sse(
          { type: 'conversation', id: 'alpha' }, { type: 'error', content: 'Synthetic retry failed' }, { type: 'done', message_id: '', outcome: 'failed' }) })
        const old = state.messages
        const question = body.edit_message_id ? { ...old[0], id: 'user-edited', content: body.message, alternatives: [old[0].id, 'user-edited'] } : old[0]
        const answer = { id: 'answer-new', role: 'assistant', content: 'A different approach.', parent_id: question.id,
          alternatives: body.edit_message_id ? ['answer-new'] : ['answer-old', 'answer-new'], model: 'test-model' }
        if (!body.edit_message_id) old[1].alternatives = answer.alternatives
        else old[0].alternatives = question.alternatives
        state.branchViews[body.edit_message_id ? old[0].id : 'answer-old'] = old
        state.messages = [question, answer]
        state.branchViews[body.edit_message_id ? question.id : answer.id] = state.messages
        state.activeLeaf = answer.id
        return route.fulfill({ contentType: 'text/event-stream', body: sse(
          { type: 'conversation', id: 'alpha' }, { type: 'token', content: answer.content }, { type: 'done', message_id: answer.id, outcome: 'completed' }) })
      }
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

test('model picker discovers additions, preserves selection on failure, and supports keyboard custom IDs', async (t) => {
  const { page, state } = await fixture(t)
  await page.getByTitle('Settings', { exact: true }).click()
  await page.getByRole('button', { name: 'Model', exact: true }).click()
  await page.getByRole('button', { name: 'Choose model', exact: true }).click()
  await page.getByRole('option', { name: 'test-model', exact: true }).waitFor()
  await page.getByRole('combobox', { name: 'Filter models' }).press('Escape')
  state.modelCatalog = { items: [
    { id: 'test-model', name: 'test-model' },
    { id: 'new-local', name: 'New local model', loaded: false, supports_tools: true, supports_vision: false, context_window: 32000 },
    { id: 'embedding-only', name: 'Embedding only', kind: 'embedding' },
  ], source: 'lmstudio', mode: 'automatic' }
  await page.getByRole('button', { name: 'Choose model', exact: true }).click()
  await page.getByRole('option', { name: /New local model/ }).waitFor()
  assert.equal(await page.getByRole('option', { name: /Embedding only/ }).count(), 0)
  await page.getByRole('combobox', { name: 'Filter models' }).fill('new-local')
  await page.getByRole('combobox', { name: 'Filter models' }).press('Enter')
  await page.waitForFunction(() => document.querySelector('[aria-label="Choose model"]')?.textContent.includes('New local model'))
  assert.equal(state.settings.model, 'new-local')
  await page.getByText(/Enable Just-In-Time loading/).waitFor()
  state.modelCatalog = { ...state.modelCatalog, status: 'error', stale: true, error: 'Could not connect to the model server.' }
  await page.getByRole('button', { name: 'Refresh models', exact: true }).click()
  await page.getByText(/Showing the last available list/).waitFor()
  assert.equal(state.settings.model, 'new-local')
  state.modelCatalog = { items: [], source: 'openai' }
  await page.getByRole('button', { name: 'Choose model', exact: true }).click()
  await page.getByRole('option', { name: /new-local.*Configured ID/ }).waitFor()
  await page.getByRole('combobox', { name: 'Filter models' }).fill('private/custom:latest')
  await page.getByRole('combobox', { name: 'Filter models' }).press('Enter')
  await page.waitForFunction(() => document.querySelector('[aria-label="Choose model"]')?.textContent.includes('private/custom:latest'))
  assert.equal(state.settings.model, 'private/custom:latest')
  assert.ok(state.modelReads >= 4)
})

test('admin discovers unsaved provider models and saves the selected default with masked credentials', async (t) => {
  const { page, state } = await fixture(t)
  state.modelCatalog = { items: [{ id: 'downloaded-model', name: 'Downloaded model', kind: 'llm' }], source: 'ollama' }
  await page.getByTitle('Settings', { exact: true }).click()
  await page.getByRole('button', { name: 'Configuration', exact: true }).click()
  await page.getByRole('button', { name: 'Add profile', exact: true }).click()
  await page.getByPlaceholder('profile name (id)', { exact: true }).fill('local')
  await page.getByPlaceholder('endpoint (e.g. http://localhost:11434/v1)', { exact: true }).fill('http://localhost:11434/v1')
  await page.getByPlaceholder('api key', { exact: true }).fill('synthetic-key')
  await page.getByRole('button', { name: 'Choose default model', exact: true }).click()
  await page.getByRole('option', { name: /Downloaded model/ }).click()
  assert.equal(state.profileSaves.length, 0)
  assert.equal(state.discoveryRequests[0].api_key, 'synthetic-key')
  assert.equal(state.discoveryRequests[0].model, null)
  assert.equal(state.discoveryRequests[0].model_discovery, 'automatic')
  await page.getByRole('button', { name: 'Save profiles', exact: true }).click()
  await page.getByText('Saved — applied live.', { exact: true }).waitFor()
  assert.equal(state.profileSaves[0].profiles[0].model, 'downloaded-model')
  assert.equal(await page.getByPlaceholder('••• set — leave blank to keep', { exact: true }).inputValue(), '')
  await page.getByLabel('Model discovery', { exact: true }).selectOption('manual')
  await page.getByLabel('Configured model IDs', { exact: true }).fill('downloaded-model, private-id')
  await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/api/admin/config/profiles') && r.request().method() === 'PUT'),
    page.getByRole('button', { name: 'Save profiles', exact: true }).click(),
  ])
  assert.equal(state.profileSaves[1].profiles[0].model_discovery, 'manual')
  assert.deepEqual(state.profileSaves[1].profiles[0].models, ['downloaded-model', 'private-id'])
})

test('phone settings give model selection and provider setup the full content width', async (t) => {
  const { page } = await fixture(t)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByTitle('Settings', { exact: true }).click()
  await page.getByLabel('Settings section', { exact: true }).selectOption('providers')
  await page.getByRole('button', { name: 'Choose model', exact: true }).click()
  const list = page.getByRole('listbox', { name: 'Available models' })
  await list.waitFor()
  const bounds = await list.boundingBox()
  assert.ok(bounds.width > 300 && bounds.x >= 0 && bounds.x + bounds.width <= 390)
  await page.getByRole('combobox', { name: 'Filter models' }).press('Escape')
  await page.getByLabel('Settings section', { exact: true }).selectOption('config')
  await page.getByRole('button', { name: 'Add profile', exact: true }).click()
  await page.getByPlaceholder('profile name (id)', { exact: true }).fill('phone-profile')
  const picker = await page.getByRole('button', { name: 'Choose default model', exact: true }).boundingBox()
  assert.ok(picker.x >= 0 && picker.x + picker.width <= 390)
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
})

test('projects can be created, selected, excluded per turn, and archived without deleting chats', async (t) => {
  const { page, state } = await fixture(t)
  state.docs = [{ id: 'project-doc', filename: 'Network notes.txt', status: 'ready' }]
  await page.getByRole('button', { name: 'Manage projects', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Projects', exact: true })
  await panel.getByLabel('Name', { exact: true }).fill('Infrastructure')
  await panel.getByLabel('Project instructions', { exact: true }).fill('Prefer repairable equipment.')
  await panel.getByRole('checkbox', { name: /Network notes/ }).check()
  await panel.getByRole('button', { name: 'Save project', exact: true }).click()
  await panel.getByText('Project saved.', { exact: true }).waitFor()
  assert.deepEqual(state.projectSaves[0].document_ids, ['project-doc'])
  await page.keyboard.press('Escape')
  await page.getByLabel('Project', { exact: true }).selectOption('project-1')
  await page.getByRole('heading', { name: 'Infrastructure', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Context · Project', exact: true }).click()
  const preview = page.getByRole('region', { name: 'Context preview', exact: true })
  await preview.getByText(/localhost/).waitFor()
  assert.equal(await preview.getByRole('checkbox', { name: 'Use personal memory', exact: true }).isChecked(), false)
  await preview.getByRole('checkbox', { name: 'Use project instructions', exact: true }).uncheck()
  await preview.getByRole('checkbox', { name: /Network notes/ }).uncheck()
  await page.getByPlaceholder('Message Phlox…').fill('Review this plan without the project background.')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByText('Approval needed', { exact: true }).waitFor()
  assert.equal(state.chatRequests[0].project_id, 'project-1')
  assert.deepEqual(state.chatRequests[0].context.excluded_document_ids, ['project-doc'])
  assert.equal(state.chatRequests[0].context.project_instructions, false)
  await page.getByRole('button', { name: 'Manage projects', exact: true }).click()
  await panel.getByLabel('Edit project', { exact: true }).selectOption('project-1')
  await panel.getByRole('checkbox', { name: /Archived/ }).check()
  await panel.getByRole('button', { name: 'Save project', exact: true }).click()
  await panel.getByText('Project saved.', { exact: true }).waitFor()
  assert.equal(state.projectSaves.at(-1).archived, true)
})

test('existing chats move into projects and saved context records remain inspectable after reload', async (t) => {
  const { page, state } = await fixture(t)
  state.projects = [{ id: 'project-1', name: 'Infrastructure', description: '', instructions: 'Prefer repairable equipment.', document_ids: [], revision: 1, archived: false }]
  state.messages.push({ id: 'answer-project', role: 'assistant', content: 'Keep local backups.', usage: { turn_id: 'turn-project', total: 25 } })
  await page.reload()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByRole('button', { name: 'Context record', exact: true }).click()
  let record = page.getByRole('region', { name: 'Context record', exact: true })
  await record.getByText('[S1] Network notes', { exact: true }).click()
  await record.getByText('Retain local backups for 30 days.', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Manage projects', exact: true }).click()
  await page.getByLabel('Edit project', { exact: true }).selectOption('project-1')
  await page.getByRole('button', { name: 'Move current chat here', exact: true }).click()
  await page.getByText('Chat updated. Close Settings to continue.', { exact: true }).waitFor()
  assert.equal(state.conversationProjects.alpha, 'project-1')
  await page.reload()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByRole('button', { name: 'Context record', exact: true }).click()
  assert.equal(await page.getByLabel('Project', { exact: true }).inputValue(), 'project-1')
  record = page.getByRole('region', { name: 'Context record', exact: true })
  await record.getByText('[S1] Network notes', { exact: true }).waitFor()
  assert.equal(state.contextReads.length, 2)
})

test('project knowledge is selected in Research and document exclusions update both controls', async (t) => {
  const { page, state } = await fixture(t)
  state.docs = [{ id: 'project-doc', filename: 'Network notes.txt', status: 'ready' }]
  state.projects = [{ id: 'project-1', name: 'Infrastructure', description: '', instructions: '', document_ids: ['project-doc'], revision: 1, archived: false }]
  await page.reload()
  await page.getByLabel('Project', { exact: true }).selectOption('project-1')
  await page.getByRole('heading', { name: 'Infrastructure', exact: true }).waitFor()
  await page.getByRole('combobox', { name: 'Chat mode' }).selectOption('research')
  await page.getByRole('combobox', { name: 'Research sources' }).selectOption('documents')
  const documents = page.getByRole('group', { name: 'Choose documents' })
  assert.equal(await documents.getByRole('checkbox', { name: 'Network notes.txt' }).isChecked(), true)
  await page.getByPlaceholder('Message Phlox…').fill('Research the retention policy.')
  assert.equal(await page.getByRole('button', { name: 'Send', exact: true }).isEnabled(), true)
  await documents.getByRole('checkbox', { name: 'Network notes.txt' }).uncheck()
  assert.equal(await page.getByRole('button', { name: 'Send', exact: true }).isEnabled(), false)
  await documents.getByRole('checkbox', { name: 'Network notes.txt' }).check()
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByText('Approval needed', { exact: true }).waitFor()
  assert.equal(state.chatRequests[0].project_id, 'project-1')
  assert.equal(state.chatRequests[0].research.scope, 'documents')
  assert.deepEqual(state.chatRequests[0].document_ids, [])
  assert.deepEqual(state.chatRequests[0].context.excluded_document_ids, [])
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
  location: { chunk: 2, page: 2, section: 'Travel policy', start: 0, end: 63, truncated: true },
  captured_at: '2026-09-07T00:00:00Z', changed: true,
})

const webSourceFixture = () => ({
  ...sourceFixture(), kind: 'web', title: 'Public travel policy', url: 'https://example.com/policy',
  location: { status: 'fetched', start: 0, end: 63, truncated: true, fetched_at: '2026-09-08T00:00:00Z' },
})

test('web citations show retained passages, original links, changed versions and removal', async (t) => {
  const { page, state } = await fixture(t)
  state.sources['source-1'] = webSourceFixture()
  state.messages.push({ id: 'web-cited', role: 'assistant', content: 'Web evidence [S1].', citations: [sourceRef] })
  await page.getByText('Approval chat', { exact: true }).click()
  const chip = page.getByRole('button', { name: 'View source S1', exact: true })
  await chip.click()
  const dialog = page.getByRole('dialog')
  const link = dialog.getByRole('link', { name: /Open original page/ })
  assert.equal(await link.getAttribute('href'), 'https://example.com/policy')
  assert.equal(await link.getAttribute('rel'), 'noopener noreferrer')
  await dialog.getByText('Characters 1–63', { exact: true }).waitFor()
  assert.equal(await dialog.getByText(/Chunk /).count(), 0)
  await dialog.getByText(/Another captured version/).waitFor()
  await dialog.getByText(state.sources['source-1'].excerpt, { exact: true }).waitFor()
  assert.equal(await dialog.locator('script').count(), 0)
  await page.keyboard.press('Escape')
  await page.reload()
  await page.getByText('Approval chat', { exact: true }).click()
  await chip.click()
  await dialog.getByRole('button', { name: 'Remove retained snapshot', exact: true }).click()
  await dialog.getByText('Retained web snapshot removed.', { exact: true }).waitFor()
  assert.equal(await dialog.locator('blockquote').count(), 0)
  assert.equal(await dialog.getByRole('link').count(), 0)
})

test('failed web citations show an unavailable fetch without an evidence passage', async (t) => {
  const { page, state } = await fixture(t)
  state.sources['source-1'] = { ...webSourceFixture(), available: false, excerpt: null,
    reason: 'HTTP 403: Access denied or payment/login required.',
    location: { status: 'http_error', http_status: 403, fetched_at: '2026-09-08T00:00:00Z' },
  }
  state.messages.push({ id: 'failed-web', role: 'assistant', content: 'Page unavailable [S1].', citations: [sourceRef] })
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByRole('button', { name: 'View source S1', exact: true }).click()
  await page.getByRole('dialog').getByText(state.sources['source-1'].reason, { exact: true }).waitFor()
  assert.equal(await page.getByRole('dialog').locator('blockquote').count(), 0)
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
  await page.getByText('Page 2', { exact: true }).waitFor()
  await page.getByText('Section: Travel policy', { exact: true }).waitFor()
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

for (const web of [false, true]) test(`approval snapshots and durable replay retain ${web ? 'web' : 'document'} citations in new tabs`, async (t) => {
  const approval = { ...pending(), content: 'Evidence [S1].', sources: [sourceRef] }
  const { page, state, context } = await fixture(t, { durable: true, approval })
  state.sources['source-1'] = web ? webSourceFixture() : sourceFixture()
  state.run = { id: 'run-1', conversation_id: 'alpha', status: 'awaiting_approval', pending_id: 'approval-1' }
  state.events = [{ type: 'sources', sources: [sourceRef] }, { type: 'token', content: 'Evidence [S1].' }]
  await openApproval(page)
  await page.getByRole('button', { name: 'View source S1', exact: true }).click()
  await page.getByRole('dialog').getByText(web ? 'Public travel policy' : 'Evidence.txt', { exact: true }).waitFor()
  await page.keyboard.press('Escape')
  await page.reload()
  await openApproval(page)
  await page.getByRole('button', { name: 'View source S1', exact: true }).waitFor()
  const other = await context.newPage()
  await other.goto(baseURL)
  await openApproval(other)
  await other.getByRole('button', { name: 'View source S1', exact: true }).click()
  await other.getByRole('dialog').getByText(web ? 'Public travel policy' : 'Evidence.txt', { exact: true }).waitFor()
  assert.ok(state.sourceReads.length >= 2 && state.sourceReads.every((id) => id === 'source-1'))
  await other.close()
  // A running subscription rebuilds the same catalog from persisted events alone.
  state.approval = null
  state.run.status = 'running'
  await page.reload()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByRole('button', { name: 'View source S1', exact: true }).click()
  await page.getByRole('dialog').getByText(web ? 'Public travel policy' : 'Evidence.txt', { exact: true }).waitFor()
})


test('document processing recovery and index rebuild show progress and failures', async (t) => {
  const { page, state } = await fixture(t)
  state.docs = [{ id: 'doc-1', filename: 'Policy.pdf', status: 'interrupted', error: 'Restart interrupted processing.',
    size_bytes: 200, n_chunks: 0, ingestion: { stage: 'interrupted' } }]
  await page.getByTitle('Settings', { exact: true }).click()
  await page.getByRole('navigation').getByRole('button', { name: 'Documents', exact: true }).click()
  await page.getByText('Embedding model changed. Rebuild required.', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Retry processing', exact: true }).click()
  await page.getByText('queued', { exact: true }).waitFor()
  assert.deepEqual(state.retries, ['doc-1'])
  state.docs[0] = { ...state.docs[0], status: 'processing', ingestion: { stage: 'embedding', completed: 64, total: 120 } }
  await page.getByText('embedding · 64/120 chunks', { exact: true }).waitFor()
  assert.equal(await page.getByRole('progressbar', { name: 'Processing Policy.pdf' }).getAttribute('value'), '64')
  state.docs[0] = { ...state.docs[0], status: 'ready', n_chunks: 120, ingestion: { stage: 'ready', completed: 120, total: 120 } }
  await page.getByRole('button', { name: 'Reprocess document', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Rebuild search index', exact: true }).click()
  await page.getByRole('button', { name: 'Rebuilding…', exact: true }).waitFor()
  assert.equal(state.rebuilds, 1)
  state.index = { ...state.index, status: 'error', error: 'Rebuild failed. Previous index preserved.' }
  await page.getByText('Rebuild failed. Previous index preserved.', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Rebuild search index', exact: true }).waitFor()
})

// Wave 9: real composer/settings, isolated APIs, no model/search credentials.
test('research is explicit, uses selected documents, and returns to Chat after send', async (t) => {
  const { page, state } = await fixture(t)
  state.docs = [{ id: 'research-doc', filename: 'Policy.md', status: 'ready', conversation_id: 'alpha' }]
  await page.getByText('Approval chat', { exact: true }).click()
  assert.equal(await page.getByRole('combobox', { name: 'Chat mode', exact: true }).inputValue(), 'chat')
  await page.getByRole('combobox', { name: 'Chat mode', exact: true }).selectOption('research')
  await page.getByRole('combobox', { name: 'Research sources', exact: true }).selectOption('documents')
  await page.getByPlaceholder('Message Phlox…').fill('Compare the policies')
  assert.equal(await page.getByRole('button', { name: 'Send', exact: true }).isDisabled(), true)
  await page.getByRole('checkbox', { name: 'Policy.md', exact: true }).check()
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByText('I can save the plan.', { exact: true }).first().waitFor()
  assert.equal(state.chatRequests.length, 1)
  assert.deepEqual(state.chatRequests[0].research, { scope: 'documents', depth: 'standard', domains: [] })
  assert.deepEqual(state.chatRequests[0].document_ids, ['research-doc'])
  assert.equal(state.chatRequests[0].auto_approve, false)
  assert.equal(await page.getByRole('combobox', { name: 'Chat mode', exact: true }).inputValue(), 'chat')
})

test('per-conversation drafts survive switching and reload', async (t) => {
  const { page } = await fixture(t)
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByPlaceholder('Message Phlox…').fill('Alpha unsent draft')
  await page.getByText('Other chat', { exact: true }).click()
  assert.equal(await page.getByPlaceholder('Message Phlox…').inputValue(), '')
  await page.getByPlaceholder('Message Phlox…').fill('Beta unsent draft')
  await page.getByText('Approval chat', { exact: true }).click()
  assert.equal(await page.getByPlaceholder('Message Phlox…').inputValue(), 'Alpha unsent draft')
  await page.reload(); await page.getByText('Approval chat', { exact: true }).click()
  assert.equal(await page.getByPlaceholder('Message Phlox…').inputValue(), 'Alpha unsent draft')
  assert.equal(await page.getByRole('combobox', { name: 'Chat mode', exact: true }).inputValue(), 'chat')
})

test('admin tests unsaved search, sees fallback, and saved keys are masked', async (t) => {
  const { page, state } = await fixture(t)
  await page.getByRole('button', { name: 'Appearance', exact: true }).click()
  await page.getByRole('button', { name: 'Configuration', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Web search configuration' })
  await panel.getByLabel('Search engine', { exact: true }).selectOption('serper')
  await panel.getByLabel('Serper API key', { exact: false }).fill('synthetic-key')
  await panel.getByRole('button', { name: 'Test search', exact: true }).click()
  await panel.getByText(/Fallback used: Primary HTTP 403/).waitFor()
  assert.equal(state.searchTests[0].serper_api_key, 'synthetic-key')
  assert.equal(state.searchSaves.length, 0)
  await panel.getByRole('button', { name: 'Save search settings', exact: true }).click()
  await panel.getByText('Saved. New searches use these settings immediately.', { exact: true }).waitFor()
  assert.equal(await panel.getByLabel('Serper API key', { exact: false }).inputValue(), '')
  await panel.getByLabel('Search engine', { exact: true }).selectOption('searxng')
  await panel.getByRole('link', { name: 'searx.space', exact: true }).waitFor()
})

test('research progress replays after reload with its plan and counters', async (t) => {
  const { page, state } = await fixture(t, { durable: true })
  state.run = { id: 'run-1', conversation_id: 'alpha', status: 'running' }
  state.events = [{ type: 'research', phase: 'gather', started_at: Date.now()/1000, plan: 'Compare dates and policy allowances.', searches: 2, reads: 1, limits: { searches: 6, reads: 8 }, source_count: 3, usage: {} }]
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByRole('region', { name: 'Research progress' }).getByText('Gathering evidence', { exact: true }).waitFor()
  await page.reload(); await page.getByText('Approval chat', { exact: true }).click()
  const progress = page.getByRole('region', { name: 'Research progress' })
  await progress.waitFor()
  assert.equal(await progress.count(), 1)
  await progress.getByText('Research plan', { exact: true }).click()
  await progress.getByText('Compare dates and policy allowances.', { exact: true }).waitFor()
  assert.match(await progress.textContent(), /2\/6 searches/)
})

test('streaming respects reading position and Jump to latest restores following', async (t) => {
  const { page, state } = await fixture(t)
  state.messages = Array.from({ length: 40 }, (_, i) => ({ id: 'msg-'+i, role: i % 2 ? 'assistant' : 'user', content: 'Message '+i+' '+('content '.repeat(60)) }))
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByText(/^Message 39 /).waitFor()
  const scroll = page.getByRole('region', { name: 'Conversation messages', exact: true })
  await scroll.evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll', { bubbles: true })) })
  await page.getByRole('button', { name: 'Jump to latest', exact: true }).waitFor()
  await page.evaluate(async () => {
    const { useStore } = await import('/src/store/useStore.js')
    useStore.setState({ streaming: true, live: { content: '', sources: [], toolCalls: [], artifacts: [], thinking: '' } })
    useStore.getState()._onEvent({ type: 'token', content: 'New streamed content.' })
  })
  assert.equal(await scroll.evaluate(el => el.scrollTop), 0)
  await page.getByRole('button', { name: 'Jump to latest', exact: true }).click()
  assert.ok(await scroll.evaluate(el => el.scrollTop > 100))
})


test('regeneration keeps alternatives navigable after reload on a phone', async t => {
  const { page, state } = await fixture(t)
  state.branchMode = true
  state.messages = [{ id: 'user-1', role: 'user', content: 'Explore the options' },
    { id: 'answer-old', role: 'assistant', content: 'The original approach.', model: 'test-model' }]
  state.activeLeaf = 'answer-old'
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByTitle('Regenerate', { exact: true }).click()
  await page.getByRole('navigation', { name: 'answer alternatives' }).getByText('2 of 2').waitFor()
  assert.equal(state.chatRequests[0].regenerate_message_id, 'answer-old')
  assert.equal(state.chatRequests[0].expected_leaf_id, 'answer-old')
  await page.getByRole('button', { name: 'Previous answer alternative' }).click()
  await page.getByText('The original approach.', { exact: true }).waitFor()
  assert.equal(state.selections[0].expected_leaf_id, 'answer-new')
  await page.reload()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByRole('navigation', { name: 'answer alternatives' }).getByText('1 of 2').waitFor()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Next answer alternative' }).click()
  await page.getByText('A different approach.', { exact: true }).waitFor()
})

test('editing preserves prompt alternatives and a failed retry leaves the old answer usable', async t => {
  const { page, state } = await fixture(t)
  state.branchMode = true
  state.messages = [{ id: 'user-1', role: 'user', content: 'Explore the options' },
    { id: 'answer-old', role: 'assistant', content: 'The original approach.', model: 'test-model' }]
  state.activeLeaf = 'answer-old'
  await page.getByText('Approval chat', { exact: true }).click()
  state.branchFailure = true
  await page.getByTitle('Regenerate', { exact: true }).click()
  await page.getByText('Synthetic retry failed', { exact: true }).waitFor()
  await page.getByText('The original approach.', { exact: true }).waitFor()
  state.branchFailure = false
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  await page.locator('textarea').first().fill('Explore a different direction')
  await page.getByRole('button', { name: 'Save & resend', exact: true }).click()
  await page.getByRole('navigation', { name: 'prompt alternatives' }).getByText('2 of 2').waitFor()
  assert.equal(state.chatRequests.at(-1).edit_message_id, 'user-1')
  assert.equal(state.chatRequests.at(-1).expected_leaf_id, 'answer-old')
  await page.getByRole('button', { name: 'Previous prompt alternative' }).click()
  await page.getByText('Explore the options', { exact: true }).waitFor()
  await page.getByText('The original approach.', { exact: true }).waitFor()
})


test('saved artifact canvas and download use answer bytes', async t => {
  const { page, state } = await fixture(t)
  state.messages.push({ id: 'answer-old', role: 'assistant', content: 'Here is your report.', artifacts: [
    { name: 'report.md', path: 'report.md', ext: '.md', snapshot_status: 'saved', url: '/api/files/alpha/saved/answer-old/0' },
  ] })
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByTitle('Open in canvas', { exact: true }).click()
  await page.getByRole('heading', { name: 'Original retained report', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Source', exact: true }).click()
  await page.getByText('# Original retained report', { exact: false }).waitFor()
  const previewReads = state.savedFileReads.length
  assert.ok(previewReads >= 1)
  const download = page.waitForEvent('download')
  await page.getByTitle('Download', { exact: true }).click()
  await download
  assert.equal(state.savedFileReads.length, previewReads + 1)
  await page.setViewportSize({ width: 390, height: 844 })
  const panel = await page.getByRole('region', { name: 'Artifact canvas', exact: true }).boundingBox()
  assert.ok(panel.x >= 0 && panel.x + panel.width <= 391, 'canvas fits phone width')
  await page.getByTitle('Close', { exact: true }).click()
  assert.equal(await page.getByRole('region', { name: 'Artifact canvas', exact: true }).count(), 0)
})

async function artifactFixture(t, content = '🌸 Original paragraph.\n\nKeep this section.') {
  const fixtureState = await fixture(t)
  const { page, state, context } = fixtureState
  const first = { id: 'v1', number: 1, origin: 'agent', content, sha256: 'original', source_message_id: 'answer-old' }
  const editor = { versions: [first], head: 'v1', workspace: { sha256: 'original', available: true, exists: true },
    saves: [], publishes: [], revisions: [], stale: false, failProposal: false, holdProposal: false }
  const detail = id => ({ id: 'artifact-1', path: 'report.md', head_version_id: editor.head,
    version: editor.versions.find(v => v.id === id) || editor.versions.at(-1),
    versions: [...editor.versions].reverse(), workspace: editor.workspace })
  await context.route(`${baseURL}/api/artifacts/**`, async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const body = route.request().method() === 'POST' ? route.request().postDataJSON() : null
    if (path.endsWith('/versions') || path.endsWith('/restore')) {
      if (editor.stale) return route.fulfill({ status: 409, json: { detail: 'A newer version was saved. Reload versions before saving; your draft is unchanged.' } })
      const source = editor.versions.find(v => v.id === (body.base_version_id || body.version_id))
      const number = editor.versions.length + 1
      const version = { ...source, id: `v${number}`, number, content: body.content ?? source.content,
        sha256: `hash-${number}`, origin: body.content !== undefined ? 'edit' : 'restore', parent_version_id: editor.head }
      editor.versions.push(version)
      editor.head = version.id
      editor.saves.push(body)
      return route.fulfill({ json: detail(version.id) })
    }
    if (path.endsWith('/publish')) {
      editor.publishes.push(body)
      editor.workspace = { ...editor.workspace, sha256: editor.versions.find(v => v.id === body.version_id).sha256 }
      return route.fulfill({ json: editor.workspace })
    }
    if (path.endsWith('/diff')) return route.fulfill({ json: { diff: '--- v1\n+++ v2\n-Original paragraph.\n+Revised paragraph.', truncated: false } })
    if (path.endsWith('/revise')) {
      editor.revisions.push(body)
      if (editor.holdProposal) await new Promise(resolve => { editor.release = resolve })
      return route.fulfill({ contentType: 'text/event-stream', body: editor.failProposal
        ? sse({ type: 'error', content: 'The model could not complete this revision. Your document is unchanged.' })
        : sse({ type: 'artifact_proposal', replacement: 'A concise paragraph.', model: 'test-model', usage: { total: 42 } }) }).catch(() => {})
    }
    if (path.includes('/download/')) return route.fulfill({ contentType: 'application/octet-stream', body: editor.versions.find(v => v.id === path.split('/').at(-1)).content })
    return route.fulfill({ json: detail(url.searchParams.get('version_id') || editor.head) })
  })
  state.messages.push({ id: 'answer-old', role: 'assistant', content: 'Here is your report.', artifacts: [
    { name: 'report.md', path: 'report.md', ext: '.md', snapshot_status: 'saved', url: '/api/files/alpha/saved/answer-old/0', artifact_id: 'artifact-1', version_id: 'v1' },
  ] })
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByTitle('Open in canvas', { exact: true }).click()
  await page.getByRole('button', { name: 'Edit & versions', exact: true }).click()
  await page.getByLabel('Artifact text', { exact: true }).waitFor()
  return { ...fixtureState, editor }
}

test('artifact editing compares, restores, downloads and explicitly updates workspace on desktop and phone', async t => {
  const { page, editor } = await artifactFixture(t)
  await page.getByLabel('Artifact text', { exact: true }).fill('Revised paragraph.\n\nKeep this section.')
  await page.getByRole('button', { name: 'Save version', exact: true }).click()
  await page.getByText('Saved version 2.', { exact: false }).waitFor()
  assert.equal(editor.publishes.length, 0)
  await page.getByRole('button', { name: 'Compare', exact: true }).click()
  await page.getByLabel('Version difference').getByText('+Revised paragraph.', { exact: false }).waitFor()
  await page.getByLabel('Artifact version', { exact: true }).selectOption('v1')
  await page.getByRole('button', { name: 'Restore as new version', exact: true }).click()
  await page.getByText('Restored as version 3.', { exact: false }).waitFor()
  assert.equal(editor.versions[2].content, editor.versions[0].content)
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download version', exact: true }).click()
  assert.equal((await downloaded).suggestedFilename(), 'report-v3.md')
  await page.getByRole('button', { name: 'Use in workspace', exact: true }).click()
  await page.getByText('Workspace updated.', { exact: false }).waitFor()
  assert.equal(editor.publishes.length, 1)
  assert.equal(editor.publishes[0].expected_workspace_sha256, 'original')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('region', { name: 'Artifact canvas', exact: true }).getByRole('button', { name: 'Edit', exact: true }).click()
  const panel = await page.getByRole('region', { name: 'Artifact canvas', exact: true }).boundingBox()
  assert.ok(panel.x >= 0 && panel.x + panel.width <= 391)
  for (const label of ['Save version', 'Use in workspace', 'Download version']) {
    const bounds = await page.getByRole('button', { name: label, exact: true }).boundingBox()
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 391, `${label} fits phone width`)
  }
  await page.getByRole('button', { name: 'Preview original', exact: true }).click()
  await page.getByRole('heading', { name: 'Original retained report', exact: true }).waitFor()
})

test('artifact draft survives stale save, canvas close and chat navigation; logout clears it', async t => {
  const { page, editor } = await artifactFixture(t)
  const text = 'Keep this unsaved draft.'
  await page.getByLabel('Artifact text', { exact: true }).fill(text)
  editor.stale = true
  await page.getByRole('button', { name: 'Save version', exact: true }).click()
  await page.getByRole('alert').getByText('A newer version was saved.', { exact: false }).waitFor()
  assert.equal(await page.getByLabel('Artifact text', { exact: true }).inputValue(), text)
  await page.getByTitle('Close', { exact: true }).click()
  await page.getByText('Other chat', { exact: true }).click()
  await page.getByText('Approval chat', { exact: true }).click()
  await page.getByTitle('Open in canvas', { exact: true }).click()
  await page.getByText('Your unsaved draft has been restored.', { exact: false }).waitFor()
  assert.equal(await page.getByLabel('Artifact text', { exact: true }).inputValue(), text)
  editor.stale = false
  await page.getByRole('button', { name: 'Reload versions', exact: true }).click()
  await page.getByRole('button', { name: 'Save version', exact: true }).click()
  await page.getByText('Saved version 2.', { exact: false }).waitFor()
  await page.getByLabel('Artifact text', { exact: true }).fill('Another private draft')
  assert.equal(await page.evaluate(async () => {
    const { useStore } = await import('/src/store/useStore.js')
    useStore.getState().logout()
    return Object.keys(useStore.getState().artifactDrafts).length
  }), 0)
})

test('selected text revision handles Unicode offsets, requires review and preserves surrounding text', async t => {
  const { page, editor } = await artifactFixture(t)
  const input = page.getByLabel('Artifact text', { exact: true })
  await input.evaluate(el => { el.focus(); el.setSelectionRange(3, 22); el.dispatchEvent(new Event('select', { bubbles: true })) })
  await page.getByLabel('Revision instruction', { exact: true }).fill('Make it concise')
  await page.getByRole('button', { name: 'Revise selection', exact: true }).click()
  await page.getByRole('button', { name: 'Apply to draft', exact: true }).waitFor()
  assert.equal(editor.revisions[0].start, 2) // 🌸 is one Unicode code point, two JS code units.
  assert.equal(editor.revisions[0].end, 21)
  assert.equal(await input.inputValue(), editor.versions[0].content)
  assert.equal(editor.saves.length, 0)
  await page.getByRole('button', { name: 'Apply to draft', exact: true }).click()
  assert.equal(await input.inputValue(), '🌸 A concise paragraph.\n\nKeep this section.')
  assert.equal(await page.getByRole('button', { name: 'Revise selection', exact: true }).isDisabled(), true)
  await page.getByRole('button', { name: 'Save version', exact: true }).click()
  await page.getByText('Saved version 2.', { exact: false }).waitFor()
  assert.equal(editor.saves[0].content, '🌸 A concise paragraph.\n\nKeep this section.')
})

test('failed or stopped artifact revisions leave saved text unchanged', async t => {
  const { page, editor } = await artifactFixture(t)
  const input = page.getByLabel('Artifact text', { exact: true })
  await input.evaluate(el => { el.focus(); el.setSelectionRange(3, 22); el.dispatchEvent(new Event('select', { bubbles: true })) })
  await page.getByLabel('Revision instruction', { exact: true }).fill('Try this revision')
  editor.failProposal = true
  await page.getByRole('button', { name: 'Revise selection', exact: true }).click()
  await page.getByRole('alert').getByText('The model could not complete this revision.', { exact: false }).waitFor()
  assert.equal(await input.inputValue(), editor.versions[0].content)
  editor.failProposal = false
  editor.holdProposal = true
  await page.getByRole('button', { name: 'Revise selection', exact: true }).click()
  await page.getByRole('button', { name: 'Stop revision', exact: true }).click()
  await page.getByText('Revision stopped.', { exact: false }).waitFor()
  editor.release?.()
  assert.equal(await input.inputValue(), editor.versions[0].content)
  assert.equal(editor.saves.length, 0)
})

test('selected revision preserves CRLF outside the passage and keyboard selection works', async t => {
  const { page, editor } = await artifactFixture(t, '🌸 First line.\r\nOriginal paragraph.\r\nKeep this section.')
  const input = page.getByLabel('Artifact text', { exact: true })
  await input.focus()
  await input.press('ControlOrMeta+A')
  await input.press('ArrowLeft')
  await input.press('ArrowDown')
  await input.press('Home')
  for (let i = 0; i < 19; i++) await input.press('Shift+ArrowRight')
  await page.getByLabel('Revision instruction', { exact: true }).fill('Shorten the paragraph')
  await page.getByRole('button', { name: 'Revise selection', exact: true }).click()
  await page.getByRole('button', { name: 'Apply to draft', exact: true }).click()
  await page.getByRole('button', { name: 'Save version', exact: true }).click()
  await page.getByText('Saved version 2.', { exact: false }).waitFor()
  assert.equal(editor.revisions[0].start, 15)
  assert.equal(editor.saves[0].content, '🌸 First line.\r\nA concise paragraph.\r\nKeep this section.')
})
