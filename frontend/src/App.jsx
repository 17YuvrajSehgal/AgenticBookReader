import { useEffect, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/TextLayer.css'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import katex from 'katex'
import 'katex/dist/katex.min.css'

pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

async function api(path, opts = {}) {
  if (opts.json) {
    opts = { ...opts, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(opts.json) }
  }
  const res = await fetch('/api' + path, opts)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const err = new Error(typeof body.detail === 'string' ? body.detail : res.statusText)
    err.status = res.status
    throw err
  }
  return res.json()
}

// Theme: follows the system until the reader picks one. The choice is remembered in this browser.
// Set before the first draw, so the page does not flash in the other theme.
try {
  const saved = localStorage.getItem('theme')
  if (saved) document.documentElement.dataset.theme = saved
} catch { /* private window: follow the system */ }

function ThemeToggle() {
  const [theme, setTheme] = useState(() =>
    document.documentElement.dataset.theme || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'))
  const other = theme === 'dark' ? 'light' : 'dark'
  const flip = () => {
    document.documentElement.dataset.theme = other
    setTheme(other)
    try { localStorage.setItem('theme', other) } catch { /* not remembered */ }
  }
  return (
    <button type="button" className="quiet" onClick={flip} aria-label={`Switch to the ${other} theme`}>
      {other === 'light' ? '☀ Light' : '☾ Dark'}
    </button>
  )
}

// Read once at start: links from emails and Google come back to the app with these.
const START = new URLSearchParams(window.location.search)
if (START.toString()) window.history.replaceState(null, '', window.location.pathname)
const LINK_ERRORS = {
  link: 'This link is not valid any more. Ask for a new one.',
  google: 'Signing in with Google did not work. Try again.',
  closed: 'New accounts are closed on this server. Ask the admin.',
  disabled: 'This account is turned off. Ask the admin.',
}

export default function App() {
  const [user, setUser] = useState(undefined) // undefined = still checking, null = signed out
  const [book, setBook] = useState(null)
  const [view, setView] = useState('library') // library | settings
  const [resetToken, setResetToken] = useState(START.get('reset') && START.get('token'))

  const loadMe = () => api('/me').then(setUser, () => setUser(null))
  useEffect(() => { loadMe() }, [])

  if (resetToken) return <ResetPassword token={resetToken} onDone={(u) => { setResetToken(null); setUser(u); loadMe() }} />
  if (user === undefined) return null
  if (!user) return <Login onLogin={loadMe} startError={LINK_ERRORS[START.get('error')]} />
  // After "delete my account" the logout call gets a 401. The reader is signed out either way.
  const logout = () => api('/logout', { method: 'POST' }).catch(() => {}).then(() => { setUser(null); setView('library') })
  if (view === 'settings') {
    return <Settings user={user} onUser={loadMe} onBack={() => { setView('library'); loadMe() }} onLogout={logout} />
  }
  if (book) return <Reader book={book} onClose={() => setBook(null)} />
  return <Library user={user} onOpen={setBook} onLogout={logout} onSettings={() => setView('settings')} />
}

// Sign in with a password, an email link or Google; or make an account.
function Login({ onLogin, startError }) {
  const [mode, setMode] = useState('signin') // signin | register | link | forgot
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState(startError || '')
  const [sent, setSent] = useState('')
  const [busy, setBusy] = useState(false)
  const [options, setOptions] = useState({})
  useEffect(() => { api('/auth/options').then(setOptions, () => {}) }, [])

  const go = (next) => { setMode(next); setError('') }
  const submit = (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    const [path, json, done] = {
      signin: ['/login/password', { email, password }, null],
      register: ['/register', { email, password, name }, 'We sent a link to confirm your email. Open it in this browser to finish.'],
      link: ['/login/link', { email }, 'We sent a sign-in link. Open it in this browser. It works once, for 15 minutes.'],
      forgot: ['/password/forgot', { email }, 'If this email has an account, we sent a link to choose a new password.'],
    }[mode]
    api(path, { method: 'POST', json })
      .then((res) => (res.sent
        ? setSent(options.mail ? done : 'No email was sent: this server has no mail server set up yet. '
          + 'The link is printed in the window where the backend runs. Copy it into this browser.')
        : onLogin()),
        (err) => setError(err.message))
      .finally(() => setBusy(false))
  }

  if (sent) {
    return (
      <div className="center">
        <h1>{options.mail ? 'Check your email' : 'Get the link from the server'}</h1>
        <p className="lede">{sent}</p>
        <button className="link" onClick={() => { setSent(''); go('signin') }}>Back to sign in</button>
      </div>
    )
  }
  const titles = { signin: 'Sign in', register: 'Create an account', link: 'Sign in with an email link', forgot: 'Reset your password' }
  const buttons = { signin: 'Sign in', register: 'Create account', link: 'Email me a link', forgot: 'Email me a reset link' }
  return (
    <form className="center auth" onSubmit={submit}>
      <h1>Reading Companion</h1>
      <p className="lede">Open a book. Get each page explained in simple words, and ask when you forget something.</p>
      <h2>{titles[mode]}</h2>
      {mode === 'register' && (
        <>
          <label htmlFor="name">Your name <span className="muted">(optional)</span></label>
          <input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
        </>
      )}
      <label htmlFor="email">Email</label>
      <input id="email" type="email" required autoFocus autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      {(mode === 'signin' || mode === 'register') && (
        <>
          <label htmlFor="password">Password {mode === 'register' && <span className="muted">(at least 8 characters)</span>}</label>
          <input id="password" type="password" required minLength={mode === 'register' ? 8 : undefined}
            autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </>
      )}
      <button className="primary" disabled={busy}>{busy ? 'Please wait…' : buttons[mode]}</button>
      {error && <p className="error" role="alert">{error}</p>}
      {options.google && mode !== 'forgot' && (
        <a className="button google" href="/api/auth/google">Continue with Google</a>
      )}
      <div className="auth-links small">
        {mode !== 'signin' && <button type="button" className="link" onClick={() => go('signin')}>Sign in with a password</button>}
        {mode !== 'link' && <button type="button" className="link" onClick={() => go('link')}>Email me a sign-in link</button>}
        {mode === 'signin' && <button type="button" className="link" onClick={() => go('forgot')}>Forgot your password?</button>}
        {mode !== 'register' && options.registration !== false && (
          <button type="button" className="link" onClick={() => go('register')}>Create an account</button>
        )}
      </div>
      <div className="account small"><ThemeToggle /></div>
    </form>
  )
}

// The page a reset link from the email opens.
function ResetPassword({ token, onDone }) {
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [error, setError] = useState('')
  const submit = (e) => {
    e.preventDefault()
    if (password !== again) return setError('The two passwords are not the same.')
    api('/password/reset', { method: 'POST', json: { token, password } }).then(onDone, (err) => setError(err.message))
  }
  return (
    <form className="center auth" onSubmit={submit}>
      <h1>Choose a new password</h1>
      <p className="lede">After this you are signed in here, and signed out everywhere else.</p>
      <label htmlFor="new-password">New password <span className="muted">(at least 8 characters)</span></label>
      <input id="new-password" type="password" required minLength={8} autoFocus autoComplete="new-password"
        value={password} onChange={(e) => setPassword(e.target.value)} />
      <label htmlFor="again">The same password again</label>
      <input id="again" type="password" required autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
      <button className="primary">Save password</button>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  )
}

// Account, Claude connection and (for admins) the list of accounts.
function Settings({ user, onUser, onBack, onLogout }) {
  return (
    <div className="center settings">
      <button className="quiet back" onClick={onBack}>‹ My books</button>
      <h1>Settings</h1>
      <ClaudeConnection user={user} onChange={onUser} />
      <AccountSettings user={user} onUser={onUser} onLogout={onLogout} />
      {['owner', 'admin'].includes(user.role) && <Admin user={user} />}
    </div>
  )
}

function ClaudeConnection({ user, onChange }) {
  const [conn, setConn] = useState(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = () => api('/connection').then(setConn, (err) => setError(err.message))
  useEffect(() => { load() }, [])
  const done = (res) => { setConn(res); setKey(''); onChange() }
  const run = (promise) => {
    setBusy(true)
    setError('')
    promise.then(done, (err) => setError(err.message)).finally(() => setBusy(false))
  }
  const saveKey = (e) => {
    e.preventDefault()
    run(api('/connection/key', { method: 'PUT', json: { key } }))
  }
  if (!conn) return <section><h2>Claude</h2>{error ? <p className="error">{error}</p> : <p className="muted">Checking…</p>}</section>

  const cc = conn.claude_code
  const now = conn.provider === 'api' ? `your API key ending in …${conn.key_hint}`
    : conn.provider === 'claude_code' ? 'Claude Code on the computer that runs this server' : null
  return (
    <section className="connection">
      <h2>Claude</h2>
      <p>{now ? <>The app uses <strong>{now}</strong>.</> : <strong className="error">Not connected yet. Add your API key below to get pages explained.</strong>}</p>
      {cc.allowed && (
        <div className="option">
          <h3>Claude Code on this computer</h3>
          <p className="muted small">
            Uses the Claude Code login of the computer this server runs on, so it counts against that Claude plan.
            Only you, the owner of this server, can use it. The app never sees the login itself.
          </p>
          {cc.status?.logged_in
            ? <p>Signed in as <strong>{cc.status.email}</strong>{cc.status.plan && <> ({cc.status.plan} plan)</>}.</p>
            : <p>Claude Code is not signed in. Open a terminal on the server and run <code>claude auth login</code>, then press Check again.</p>}
          <div className="row">
            {conn.provider !== 'claude_code' && (
              <button className="primary" disabled={busy} onClick={() => run(api('/connection/provider', { method: 'PUT', json: { provider: 'claude_code' } }))}>
                Use Claude Code
              </button>
            )}
            <button disabled={busy} onClick={() => run(api('/connection'))}>Check again</button>
          </div>
        </div>
      )}
      <form className="option" onSubmit={saveKey}>
        <h3>Your Anthropic API key</h3>
        <p className="muted small">
          Make a key at <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">console.anthropic.com</a>.
          Use is billed to your Anthropic account. The key is stored encrypted and is never shown again, here or anywhere else.
        </p>
        {conn.has_key && <p>Saved key: <code>sk-ant-…{conn.key_hint}</code></p>}
        <label htmlFor="api-key">{conn.has_key ? 'Replace it with a new key' : 'API key'}</label>
        <input id="api-key" type="password" autoComplete="off" spellCheck="false" placeholder="sk-ant-…"
          value={key} onChange={(e) => setKey(e.target.value)} />
        <div className="row">
          <button className="primary" disabled={busy || key.trim().length < 20}>{busy ? 'Checking the key…' : 'Save key'}</button>
          {conn.has_key && conn.provider !== 'api' && (
            <button type="button" disabled={busy} onClick={() => run(api('/connection/provider', { method: 'PUT', json: { provider: 'api' } }))}>Use the saved key</button>
          )}
          {conn.has_key && (
            <button type="button" className="link danger" disabled={busy}
              onClick={() => window.confirm('Remove your saved API key?') && run(api('/connection/key', { method: 'DELETE' }))}>Remove key</button>
          )}
        </div>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      {!cc.allowed && user.role !== 'user' && (
        <p className="muted small">Claude Code on the server is only for the owner of this server.</p>
      )}
    </section>
  )
}

function AccountSettings({ user, onUser, onLogout }) {
  const [name, setName] = useState(user.name)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const say = (text) => { setNote(text); setError('') }
  const fail = (err) => { setError(err.message); setNote('') }

  const saveName = (e) => {
    e.preventDefault()
    api('/me', { method: 'PUT', json: { name } }).then(() => { say('Name saved.'); onUser() }, fail)
  }
  const savePassword = (e) => {
    e.preventDefault()
    api('/me/password', { method: 'PUT', json: { current, new: next } }).then(() => {
      setCurrent(''); setNext('')
      say('Password saved. You were signed out on your other devices.')
      onUser()
    }, fail)
  }
  const signOutOthers = () => api('/logout/others', { method: 'POST' })
    .then((res) => say(`Signed out on ${res.signed_out} other ${res.signed_out === 1 ? 'device' : 'devices'}.`), fail)
  const removeAccount = () => {
    if (!window.confirm('Delete your account, all books, chats and your skills? This cannot be undone.')) return
    api('/me', { method: 'DELETE' }).then(onLogout, fail)
  }

  return (
    <section>
      <h2>Account</h2>
      <p>{user.email}{user.role !== 'user' && <span className="muted"> · {user.role}</span>}</p>
      <form className="row" onSubmit={saveName}>
        <label htmlFor="my-name" className="sr-only">Name</label>
        <input id="my-name" placeholder="Your name" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
        <button disabled={name === user.name}>Save name</button>
      </form>
      <form className="option" onSubmit={savePassword}>
        <h3>{user.has_password ? 'Change your password' : 'Set a password'}</h3>
        {!user.has_password && <p className="muted small">You sign in with an email link{user.has_google ? ' or Google' : ''}. A password lets you sign in without email too.</p>}
        {user.has_password && (
          <>
            <label htmlFor="current-password">Current password</label>
            <input id="current-password" type="password" required autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </>
        )}
        <label htmlFor="next-password">New password <span className="muted">(at least 8 characters)</span></label>
        <input id="next-password" type="password" required minLength={8} autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        <div className="row"><button className="primary">Save password</button></div>
      </form>
      {note && <p role="status">{note}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="account small">
        <button className="link" onClick={signOutOthers}>Sign out on other devices</button>
        <a href="/api/export" download>Export my data</a>
        <button className="link" onClick={onLogout}>Sign out</button>
        {user.role !== 'owner' && <button className="link danger" onClick={removeAccount}>Delete my account</button>}
      </div>
    </section>
  )
}

const shortDate = (d) => (d ? new Date(d).toLocaleDateString('en-CA') : 'never')

// Every account on this server. Admins can turn accounts off or remove them; only the owner makes admins.
function Admin({ user }) {
  const [users, setUsers] = useState(null)
  const [error, setError] = useState('')
  const load = () => api('/admin/users').then(setUsers, (err) => setError(err.message))
  useEffect(() => { load() }, [])
  const change = (u, json) => api(`/admin/users/${u.id}`, { method: 'PUT', json }).then(load, (err) => setError(err.message))
  const remove = (u) => window.confirm(`Delete ${u.email} with all their books and notes? This cannot be undone.`) &&
    api(`/admin/users/${u.id}`, { method: 'DELETE' }).then(load, (err) => setError(err.message))
  if (!users) return null
  return (
    <section>
      <h2>Accounts on this server</h2>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="table-scroll">
        <table className="admin">
          <thead><tr><th>Account</th><th>Role</th><th>Books</th><th>Tokens</th><th>Last sign-in</th><th>Claude</th><th /></tr></thead>
          <tbody>
            {users.map((u) => {
              const fixed = u.role === 'owner' || u.id === user.id
              return (
                <tr key={u.id} className={u.disabled ? 'off' : ''}>
                  <td>{u.email}{u.name && <div className="muted small">{u.name}</div>}
                    {!u.email_verified && <div className="muted small">email not confirmed</div>}
                    {u.disabled && <div className="error small">turned off</div>}</td>
                  <td>{u.role}</td>
                  <td>{number(u.books)}</td>
                  <td>{number(u.tokens)}</td>
                  <td>{shortDate(u.last_sign_in)}</td>
                  <td>{u.connection === 'api' ? 'API key' : u.connection === 'claude_code' ? 'Claude Code' : u.role === 'owner' ? 'Claude Code' : '—'}</td>
                  <td className="actions">
                    {!fixed && user.role === 'owner' && (
                      <button className="link" onClick={() => change(u, { role: u.role === 'admin' ? 'user' : 'admin' })}>
                        {u.role === 'admin' ? 'Remove admin' : 'Make admin'}
                      </button>
                    )}
                    {!fixed && <button className="link" onClick={() => change(u, { disabled: !u.disabled })}>{u.disabled ? 'Turn on' : 'Turn off'}</button>}
                    {!fixed && <button className="link danger" onClick={() => remove(u)}>Delete</button>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

// File names make poor titles: "[O'Reilly] AI Engineering_Building..." Shown cleaner, saved as is.
const cleanTitle = (title) => title.replace(/^\[[^\]]*\]\s*/, '').replaceAll('_', ' ')

function Library({ user, onOpen, onLogout, onSettings }) {
  const [books, setBooks] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/books').then(setBooks, (err) => setError(err.message))
  }, [])

  const upload = (e) => {
    const file = e.target.files[0]
    if (!file) return
    const body = new FormData()
    body.append('file', file)
    setBusy(true)
    setError('')
    api('/books', { method: 'POST', body })
      .then(onOpen, (err) => setError(err.message))
      .finally(() => setBusy(false))
  }

  const removeBook = (book) => {
    if (!window.confirm(`Delete “${book.title}” with its notes and chats? This cannot be undone.`)) return
    api(`/books/${book.id}`, { method: 'DELETE' }).then(
      () => setBooks((all) => all.filter((b) => b.id !== book.id)),
      (err) => setError(err.message),
    )
  }

  return (
    <div className="center">
      <h1>My books</h1>
      {!user.connection && (
        <div className="suggestion">
          <p>To get pages explained, connect Claude first: add your Anthropic API key.</p>
          <button className="primary" onClick={onSettings}>Connect Claude</button>
        </div>
      )}
      {books.length === 0 && !error && (
        <p className="lede">No books yet. Add a PDF and start on page 1.</p>
      )}
      <label className="button primary">
        {busy ? 'Uploading…' : 'Add a PDF book'}
        <input type="file" accept="application/pdf" hidden disabled={busy} onChange={upload} />
      </label>
      {error && <p className="error">{error}</p>}
      {books.length > 0 && (
        <ul className="books">
          {books.map((b) => (
            <li key={b.id}>
              <button className="title" onClick={() => onOpen(b)}>{cleanTitle(b.title)}</button>
              <span className="place muted small">
                {b.status === 'ready' ? (
                  <>
                    <progress value={b.last_page} max={b.page_count} aria-label="How far you are in this book" />
                    page {b.last_page} of {b.page_count}
                  </>
                ) : b.status === 'error' ? 'Could not read this file' : 'Getting the book ready…'}
              </span>
              <button className="quiet small" onClick={() => removeBook(b)} aria-label={`Delete ${cleanTitle(b.title)}`}>Delete</button>
            </li>
          ))}
        </ul>
      )}
      <Usage />
      <div className="account small">
        <span className="muted">{user.name || user.email}</span>
        <ThemeToggle />
        <button className="link" onClick={onSettings}>Settings</button>
        <button className="link" onClick={onLogout}>Sign out</button>
      </div>
    </div>
  )
}

const number = (n) => Number(n).toLocaleString('en-US')

// How much of the LLM each feature used. Loaded only when opened.
function Usage() {
  const [usage, setUsage] = useState(null)
  const load = (e) => {
    if (e.target.open) api('/usage').then(setUsage, () => {})
  }
  const cardTokens = usage?.by_feature
    .filter((f) => f.feature.startsWith('page_'))
    .reduce((sum, f) => sum + f.tokens_in + f.tokens_out, 0)
  return (
    <details className="usage" onToggle={load}>
      <summary>LLM usage</summary>
      {usage && usage.by_feature.length === 0 && <p className="muted">No LLM calls yet.</p>}
      {usage && usage.by_feature.length > 0 && (
        <>
          {usage.pages_explained > 0 && (
            <p className="muted">
              {number(usage.pages_explained)} pages explained, about {number(Math.round(cardTokens / usage.pages_explained))} tokens per page.
            </p>
          )}
          <table>
            <thead><tr><th>Feature</th><th>Model</th><th>Calls</th><th>Tokens in</th><th>Tokens out</th><th>Avg time</th></tr></thead>
            <tbody>
              {usage.by_feature.map((f) => (
                <tr key={f.feature + f.model}>
                  <td>{f.feature.replaceAll('_', ' ')}</td><td>{f.model}</td><td>{number(f.calls)}</td>
                  <td>{number(f.tokens_in)}</td><td>{number(f.tokens_out)}</td><td>{(f.avg_ms / 1000).toFixed(1)} s</td>
                </tr>
              ))}
            </tbody>
          </table>
          <table>
            <thead><tr><th>Day</th><th>Calls</th><th>Tokens in</th><th>Tokens out</th></tr></thead>
            <tbody>
              {usage.by_day.map((d) => (
                <tr key={d.day}><td>{d.day}</td><td>{number(d.calls)}</td><td>{number(d.tokens_in)}</td><td>{number(d.tokens_out)}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </details>
  )
}

const SELECT_ACTIONS = [['simplify', 'Simplify'], ['explain', 'Explain'], ['define', 'Define']]

// The answer to Simplify / Explain / Define on text the reader selected in the book.
function SelectionNote({ note, onClose }) {
  return (
    <div className="suggestion">
      <div className="row">
        <strong>{SELECT_ACTIONS.find(([id]) => id === note.action)[1]}</strong>
        <button className="quiet" onClick={onClose} aria-label="Close this note">✕</button>
      </div>
      <blockquote>{note.text.length > 160 ? note.text.slice(0, 160) + '…' : note.text}</blockquote>
      {note.error
        ? <p className="error">{note.error}</p>
        : <p className="pre">{note.result ?? 'Working on it…'}</p>}
    </div>
  )
}

function Reader({ book: initial, onClose }) {
  const [book, setBook] = useState(initial)
  const [page, setPage] = useState(initial.last_page)
  const [numPages, setNumPages] = useState(initial.page_count)
  const paneRef = useRef(null)
  const [width, setWidth] = useState(0)

  // Fit the page to the left pane.
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)))
    observer.observe(paneRef.current)
    return () => observer.disconnect()
  }, [])

  const [tab, setTab] = useState('page')

  // Auto read: explain every page as it opens. Off by default; then the reader presses Explain now.
  // Remembered in this browser.
  const [autoRead, setAutoRead] = useState(() => {
    try { return localStorage.getItem('autoRead') === 'on' } catch { return false }
  })
  const toggleAutoRead = (on) => {
    setAutoRead(on)
    try { localStorage.setItem('autoRead', on ? 'on' : 'off') } catch { /* private window: not remembered */ }
  }

  // The bar between the page and the notes can be dragged. The width is remembered in this browser.
  const asideRef = useRef(null)
  const [panelWidth, setPanelWidth] = useState(() => {
    try { return Number(localStorage.getItem('panelWidth')) || null } catch { return null }
  })
  const resize = (width) => {
    const least = 340 // both sides stay usable. Same as the notes panel's min-width in app.css (20rem)
    // The page and the notes share what is left beside the contents list.
    const shared = asideRef.current.offsetWidth + paneRef.current.offsetWidth
    const clamped = Math.round(Math.min(Math.max(width, least), shared - least))
    setPanelWidth(clamped)
    try { localStorage.setItem('panelWidth', clamped) } catch { /* not remembered */ }
  }
  const resetWidth = () => {
    setPanelWidth(null)
    try { localStorage.removeItem('panelWidth') } catch { /* nothing to forget */ }
  }
  const onDividerKey = (e) => {
    const step = { ArrowLeft: 40, ArrowRight: -40 }[e.key]
    if (!step) return
    e.stopPropagation() // the arrow keys turn pages everywhere else
    resize(asideRef.current.offsetWidth + step)
  }

  // How the book page looks: normal (as printed), dim (less bright), night (dark page, light text).
  // Remembered in this browser.
  const [look, setLook] = useState(() => {
    try {
      // 'dimPage' is the older on/off switch; keep that choice.
      return localStorage.getItem('pageLook') || (localStorage.getItem('dimPage') === 'on' ? 'dim' : 'normal')
    } catch { return 'normal' }
  })
  const chooseLook = (value) => {
    setLook(value)
    try { localStorage.setItem('pageLook', value) } catch { /* not remembered */ }
  }

  // Wait until the backend has parsed the book and built the search index.
  useEffect(() => {
    const working = book.status === 'parsing' ||
      (book.status === 'ready' && ['pending', 'indexing'].includes(book.index_status))
    if (!working) return
    const timer = setTimeout(() => api(`/books/${book.id}`).then(setBook, () => {}), 1500)
    return () => clearTimeout(timer)
  }, [book])

  const go = (n) => setPage(Math.min(Math.max(1, n || 1), numPages || 1))

  // Goes up when the reader profile changes, so the card and the skills list load again.
  const [profileVersion, setProfileVersion] = useState(0)
  const profileChanged = () => setProfileVersion((v) => v + 1)
  // Pages saved so far by a whole-PDF run. When it goes up, the page card and the notes load again.
  const [cardsMade, setCardsMade] = useState(0)

  // A page that was open for a while and then left counts as "read". The backend decides if that
  // is a sign of comfort. Under 20 seconds is flipping; over 15 minutes the reader was away.
  useEffect(() => {
    const start = Date.now()
    return () => {
      const seconds = (Date.now() - start) / 1000
      if (seconds >= 20 && seconds <= 900) {
        api(`/books/${book.id}/pages/${page}/signal`, { method: 'POST', json: { signal: 'read' } }).catch(() => {})
      }
    }
  }, [book.id, page])

  useEffect(() => {
    api(`/books/${book.id}/last_page`, { method: 'PUT', json: { page } }).catch(() => {})
  }, [book.id, page])

  // The book's own table of contents: the list on the left, and the progress inside the current chapter.
  const [toc, setToc] = useState([])
  useEffect(() => {
    if (book.status === 'ready') api(`/books/${book.id}/toc`).then(setToc, () => {})
  }, [book.id, book.status])
  // The book cut into chapters. Notes, chats and highlights are grouped by these.
  const [chapters, setChapters] = useState([])
  useEffect(() => {
    if (book.status === 'ready') api(`/books/${book.id}/chapters`).then(setChapters, () => {})
  }, [book.id, book.status])
  const chapter = chapterOf(chapters, page)

  // Highlights of the whole book: drawn on the page, and listed in the Notes tab.
  const [highlights, setHighlights] = useState([])
  useEffect(() => {
    api(`/books/${book.id}/highlights`).then(setHighlights, () => {})
  }, [book.id])
  const removeHighlight = (id) => {
    setPicked(null)
    api(`/highlights/${id}`, { method: 'DELETE' }).then(() => setHighlights((all) => all.filter((h) => h.id !== id)), () => {})
  }

  // A chat picked in the Notes tab opens in the Chat tab.
  const [chatRequest, setChatRequest] = useState(null)
  const openChat = (id) => {
    setChatRequest({ id })
    setTab('chat')
  }
  // A question from the chapter guide starts a new chat about those pages.
  const askChapter = (scope, question) => {
    setChatRequest({ scope, question })
    setTab('chat')
  }

  // The contents list can be closed. Remembered in this browser.
  const [tocOpen, setTocOpen] = useState(() => {
    try { return localStorage.getItem('tocOpen') !== 'off' } catch { return true }
  })
  const showToc = (open) => {
    setTocOpen(open)
    try { localStorage.setItem('tocOpen', open ? 'on' : 'off') } catch { /* not remembered */ }
  }
  const goFromToc = (n) => {
    go(n)
    if (window.innerWidth <= 820) showToc(false) // on a small screen the list covers the page
  }

  // Text selected on the book page gets a small toolbar: Simplify, Explain, Define.
  const [selection, setSelection] = useState(null) // { text, x, y, rects }
  const [picked, setPicked] = useState(null)       // a highlight the reader clicked: { id, x, y }
  const [note, setNote] = useState(null)           // { action, text, result?, error? }
  useEffect(() => {
    setSelection(null)
    setPicked(null)
    setNote(null)
  }, [page])

  // x, y: where the mouse was let go.
  const onSelect = (x, y) => {
    const sel = window.getSelection()
    const text = sel.toString().replace(/\s+/g, ' ').trim()
    const sheet = paneRef.current.querySelector('.react-pdf__Page')?.getBoundingClientRect()
    if (!sheet) return
    if (!text || !paneRef.current.contains(sel.anchorNode)) {
      // A plain click. On a highlight, it offers to remove it.
      const px = (x - sheet.left) / sheet.width
      const py = (y - sheet.top) / sheet.height
      const hit = highlights.find((h) => h.page_no === page &&
        h.rects.some(([l, t, w, hgt]) => px >= l && px <= l + w && py >= t && py <= t + hgt))
      setSelection(null)
      return setPicked(hit ? { id: hit.id, x, y } : null)
    }
    setPicked(null)
    const range = sel.getRangeAt(0)
    const box = range.getBoundingClientRect()
    setSelection({ text: text.slice(0, 2000), x: box.left + box.width / 2, y: box.top, rects: lineRects(range, sheet) })
  }

  const highlight = (color) => {
    const { text, rects } = selection
    setSelection(null)
    window.getSelection().removeAllRanges()
    if (rects.length === 0) return
    api(`/books/${book.id}/highlights`, { method: 'POST', json: { page, color, text, rects } })
      .then((h) => setHighlights((all) => [...all, h]), () => {})
  }

  const act = (action) => {
    const { text } = selection
    const mine = (n) => n && n.text === text && n.action === action // ignore an answer to an older click
    setSelection(null)
    setTab('page')
    setNote({ action, text })
    api(`/books/${book.id}/pages/${page}/select`, { method: 'POST', json: { action, text } }).then(
      (res) => setNote((n) => (mine(n) ? { ...n, result: res.result } : n)),
      (err) => setNote((n) => (mine(n) ? { ...n, error: err.message } : n)),
    )
  }

  useEffect(() => {
    const onKey = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return
      if (e.key === 'ArrowRight') setPage((p) => Math.min(p + 1, numPages || p))
      if (e.key === 'ArrowLeft') setPage((p) => Math.max(p - 1, 1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [numPages])

  return (
    <div className="reader">
      <header>
        <button className="quiet" onClick={onClose}>‹ My books</button>
        {toc.length > 0 && (
          <button className="quiet" aria-expanded={tocOpen} aria-controls="contents" onClick={() => showToc(!tocOpen)}>
            ☰ Contents
          </button>
        )}
        <div className="where">
          <strong>{cleanTitle(book.title)}</strong>
          {chapter && (
            <span className="muted small">
              {chapter.title}, page {page - chapter.start + 1} of {chapter.end - chapter.start + 1}
            </span>
          )}
        </div>
        <ThemeToggle />
        <select className="look" value={look} onChange={(e) => chooseLook(e.target.value)} aria-label="How the book page looks"
          title="Dim makes the white page less bright. Night gives a dark page with light text.">
          <option value="normal">Normal page</option>
          <option value="dim">Dim page</option>
          <option value="night">Night page</option>
        </select>
        <label className="switch" title="On: every page you open is explained by itself. Off: press Explain now on the pages you want.">
          <input type="checkbox" role="switch" checked={autoRead} onChange={(e) => toggleAutoRead(e.target.checked)} />
          <span className="track" aria-hidden="true" />
          Auto read
        </label>
        <nav aria-label="Pages">
          <button onClick={() => go(page - 1)} disabled={page <= 1} aria-label="Previous page">‹</button>
          <input type="number" min="1" max={numPages || 1} value={page} aria-label="Page number"
            onChange={(e) => go(e.target.valueAsNumber)} />
          <span className="muted">of {numPages || '…'}</span>
          <button onClick={() => go(page + 1)} disabled={!numPages || page >= numPages} aria-label="Next page">›</button>
        </nav>
        {chapter && (
          <progress value={page - chapter.start + 1} max={chapter.end - chapter.start + 1} aria-label="Progress in this chapter" />
        )}
      </header>
      {selection && (
        <div className="seltools" role="toolbar" aria-label="Help with the selected text"
          style={{ left: selection.x, top: Math.max(8, selection.y - 46) }}
          onMouseDown={(e) => e.preventDefault() /* keep the text selected */}>
          {SELECT_ACTIONS.map(([id, label]) => <button key={id} onClick={() => act(id)}>{label}</button>)}
          <span className="inks" role="group" aria-label="Highlight">
            {INKS.map((c) => (
              <button key={c} className={`ink ${c}`} onClick={() => highlight(c)} aria-label={`Highlight in ${c}`} title={`Highlight in ${c}`} />
            ))}
          </span>
        </div>
      )}
      {picked && (
        <div className="seltools" role="toolbar" aria-label="This highlight" style={{ left: picked.x, top: Math.max(8, picked.y - 46) }}>
          <button onClick={() => removeHighlight(picked.id)}>Remove highlight</button>
        </div>
      )}
      <main>
        {tocOpen && toc.length > 0 && <Contents toc={toc} page={page} go={goFromToc} onClose={() => showToc(false)} />}
        <section className={`pdf ${look}`} ref={paneRef}
          onMouseUp={({ clientX, clientY }) => setTimeout(() => onSelect(clientX, clientY))}
          onScroll={() => { setSelection(null); setPicked(null) }}>
          <Document file={`/api/books/${book.id}/file`} onLoadSuccess={(pdf) => setNumPages(pdf.numPages)}
            loading={<p className="muted">Opening the book…</p>} error={<p className="error">Could not open this PDF.</p>}>
            {width > 0 && (
              <Page pageNumber={page} width={Math.min(width - 32, 1000)}>
                {/* Drawn over the page. Clicks go through to the text under them. */}
                {highlights.filter((h) => h.page_no === page).flatMap((h) => h.rects.map(([l, t, w, hgt], i) => (
                  <div key={`${h.id}-${i}`} className={`hl ${h.color}`}
                    style={{ left: `${l * 100}%`, top: `${t * 100}%`, width: `${w * 100}%`, height: `${hgt * 100}%` }} />
                )))}
              </Page>
            )}
          </Document>
        </section>
        <div className="divider" role="separator" aria-orientation="vertical" tabIndex={0}
          aria-label="Resize the notes panel. Drag, or use the left and right arrow keys. Double click to reset."
          title="Drag to resize. Double click to reset."
          onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
          onPointerMove={(e) => e.currentTarget.hasPointerCapture(e.pointerId) && resize(window.innerWidth - e.clientX)}
          onDoubleClick={resetWidth} onKeyDown={onDividerKey} />
        <aside ref={asideRef} style={panelWidth ? { flex: `0 0 ${panelWidth}px`, maxWidth: 'calc(100vw - 20rem)' } : undefined}>
          <div className="tabs" role="tablist">
            {[['page', 'Page'], ['chapter', 'Chapter'], ['chat', 'Chat'], ['notes', 'Notes'], ['skills', 'My skills']].map(([id, label]) => (
              <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
          {/* All stay mounted, so a chat is not lost when you look at the page card. */}
          <div hidden={tab !== 'page'}>
            {note && <SelectionNote note={note} onClose={() => setNote(null)} />}
            <PageCard key={page} book={book} page={page} version={`${profileVersion}-${cardsMade}`} onProfileChange={profileChanged} autoRead={autoRead} />
          </div>
          <div hidden={tab !== 'chapter'}>
            <ChapterGuide book={book} page={page} toc={toc} numPages={numPages} go={go} active={tab === 'chapter'} onAsk={askChapter} />
          </div>
          <div hidden={tab !== 'chat'} className="chat-tab">
            <Chat book={book} page={page} go={go} onProfileChange={profileChanged} chapters={chapters} request={chatRequest} />
          </div>
          <div hidden={tab !== 'notes'}>
            <Notebook book={book} page={page} chapters={chapters} highlights={highlights} go={go} active={tab === 'notes'}
              openChat={openChat} removeHighlight={removeHighlight} onBookChange={setBook} made={cardsMade} onProgress={setCardsMade} />
          </div>
          <div hidden={tab !== 'skills'}>
            <Skills active={tab === 'skills'} version={profileVersion} onProfileChange={profileChanged} />
          </div>
        </aside>
      </main>
    </div>
  )
}

// The flat list from the server ([{level, title, page}] in book order) as a tree.
// Each node also knows its parent, so the path to the current section can be opened.
function tocTree(toc) {
  const roots = []
  const open = [] // the chain of entries we are inside of, one per level
  toc.forEach((entry, id) => {
    open.length = Math.min(open.length, entry.level - 1)
    const node = { ...entry, id, parent: open.at(-1) ?? null, children: [] }
    ;(node.parent ? node.parent.children : roots).push(node)
    open.push(node)
  })
  return roots
}

// The table of contents, on the left of the page. Sections open and close; the title goes to the page.
function Contents({ toc, page, go, onClose }) {
  const roots = tocTree(toc)
  const current = toc.findLastIndex((e) => e.page <= page) // the section the reader is in
  const [opened, setOpened] = useState(() => new Set())
  const toggle = (id) => setOpened((ids) => {
    const next = new Set(ids)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  // The chapters above the current section are always open, so the reader can see where they are.
  const onPath = new Set()
  const find = (nodes) => nodes.some((n) => (n.id === current || find(n.children)) && onPath.add(n.id))
  find(roots)

  const list = (nodes) => (
    <ul>
      {nodes.map((n) => {
        const isOpen = opened.has(n.id) || (onPath.has(n.id) && n.id !== current)
        return (
          <li key={n.id}>
            <div className="entry" aria-current={n.id === current ? 'true' : undefined}>
              {n.children.length > 0 ? (
                <button className="twist" aria-expanded={isOpen} aria-label={`${isOpen ? 'Close' : 'Open'} ${n.title}`}
                  onClick={() => toggle(n.id)}>▸</button>
              ) : <span className="twist" />}
              <button className="goto" onClick={() => go(n.page)}>{n.title}</button>
            </div>
            {isOpen && n.children.length > 0 && list(n.children)}
          </li>
        )
      })}
    </ul>
  )

  return (
    <nav className="contents" id="contents" aria-label="Table of contents">
      <div className="row">
        <strong>Contents</strong>
        <button className="quiet" onClick={onClose} aria-label="Close the contents">✕</button>
      </div>
      {list(roots)}
    </nav>
  )
}

function PageLink({ page, go }) {
  return <button onClick={() => go(page)}>page {page}</button>
}

const INKS = ['yellow', 'green', 'pink', 'blue']

// LaTeX from the LLM, drawn as a real formula. Bad LaTeX is shown as plain text in red, never as an
// error. KaTeX's default (trust: false) blocks \href and other commands that could run or link.
function Formula({ latex }) {
  const html = katex.renderToString(latex.trim().replace(/^\$+|\$+$/g, ''), { displayMode: true, throwOnError: false })
  return <div className="formula" dangerouslySetInnerHTML={{ __html: html }} />
}

const chapterOf = (chapters, page) => chapters.findLast((c) => c.start <= page)

// The selected text as one box per line: [left, top, width, height], each a part of the page size.
// The browser gives one box per piece of text, often several on a line and some twice.
function lineRects(range, sheet) {
  const boxes = [...range.getClientRects()]
    .filter((r) => r.width > 1 && r.height > 1 && r.height < sheet.height * 0.15) // not the box of the whole text layer
    .sort((a, b) => a.top - b.top || a.left - b.left)
  const lines = []
  for (const r of boxes) {
    const line = lines.find((l) =>
      Math.min(l.bottom, r.bottom) - Math.max(l.top, r.top) > Math.min(l.bottom - l.top, r.height) / 2 && // same line
      r.left <= l.right + 8 && r.right >= l.left - 8)                                                      // and touching
    if (line) {
      Object.assign(line, {
        left: Math.min(line.left, r.left), right: Math.max(line.right, r.right),
        top: Math.min(line.top, r.top), bottom: Math.max(line.bottom, r.bottom),
      })
    } else lines.push({ left: r.left, right: r.right, top: r.top, bottom: r.bottom })
  }
  const part = (v) => Math.round(Math.min(Math.max(v, 0), 1) * 10000) / 10000
  return lines.map((l) => {
    const left = part((l.left - sheet.left) / sheet.width)
    const top = part((l.top - sheet.top) / sheet.height)
    return [left, top, part((l.right - sheet.left) / sheet.width - left), part((l.bottom - sheet.top) / sheet.height - top)]
  }).filter(([, , w, h]) => w > 0 && h > 0)
}

// Everything the reader saved in this book, under the chapter it belongs to: the chapter summary,
// page summaries, chats and highlights. Nothing here calls the LLM.
function Notebook({ book, page, chapters, highlights, go, active, openChat, removeHighlight, onBookChange, made, onProgress }) {
  const [notes, setNotes] = useState(null)
  useEffect(() => {
    if (active) api(`/books/${book.id}/notes`).then(setNotes, () => {})
  }, [book.id, active, made])

  if (!notes) return null
  const panel = <WholePdf book={book} active={active} onBookChange={onBookChange} onProgress={onProgress} />
  const inside = (c, p) => p >= c.start && p <= c.end
  const groups = chapters.map((c) => ({
    ...c,
    guide: notes.guides.find((g) => inside(c, g.start_page)),
    cards: notes.cards.filter((x) => inside(c, x.page_no)),
    chats: notes.chats.filter((x) => inside(c, x.page_no)),
    marks: highlights.filter((x) => inside(c, x.page_no)),
  })).filter((g) => g.guide || g.cards.length + g.chats.length + g.marks.length > 0)

  if (groups.length === 0) {
    return (
      <div className="notebook">
        {panel}
        <p className="muted">
          Nothing saved yet. Page summaries, chats and the text you highlight are collected here, chapter by chapter.
        </p>
      </div>
    )
  }
  const count = (n, one, many) => (n > 0 ? [`${n} ${n === 1 ? one : many}`] : [])
  return (
    <div className="notebook">
      {panel}
      {groups.map((g) => (
        <details key={g.start} open={inside(g, page)}>
          <summary>
            <strong>{g.title}</strong>
            <span className="muted small">
              {[...count(g.chats.length, 'chat', 'chats'), ...count(g.marks.length, 'highlight', 'highlights'),
                ...count(g.cards.length, 'page summary', 'page summaries')].join(', ')}
            </span>
          </summary>
          {g.guide && <p className="summary">{g.guide.summary}</p>}
          {g.chats.length > 0 && (
            <>
              <h2>Chats</h2>
              <ul>
                {g.chats.map((c) => (
                  <li key={c.id}>
                    <button className="link" onClick={() => openChat(c.id)}>{c.title}</button>{' '}
                    <span className="muted small">page {c.page_no}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {g.marks.length > 0 && (
            <>
              <h2>Highlights</h2>
              <ul>
                {g.marks.map((h) => (
                  <li key={h.id} className={`mark ${h.color}`}>
                    <q>{h.text}</q>
                    <span className="pages">
                      <PageLink page={h.page_no} go={go} />
                      <button onClick={() => removeHighlight(h.id)} aria-label="Remove this highlight">Remove</button>
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {g.cards.length > 0 && (
            <>
              <h2>Page summaries</h2>
              <ul>
                {g.cards.map((c) => (
                  <li key={c.page_no} className="pagesum">
                    <span className="pages"><PageLink page={c.page_no} go={go} /></span>
                    {c.card.section && <span className="muted small"> {c.card.section}</span>}
                    <ul>{c.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul>
                    {c.card.formulas?.length > 0 && (
                      <ul className="formulas">
                        {c.card.formulas.map((f, i) => <li key={i}><Formula latex={f.formula} />{f.meaning}</li>)}
                      </ul>
                    )}
                    {c.card.concerns?.length > 0 && (
                      <ul className="concerns">{c.card.concerns.map((x, i) => <li key={i}>{x}</li>)}</ul>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </details>
      ))}
    </div>
  )
}

// Make the page summary of every page in a range, in the background, and save them all as one .md file.
function WholePdf({ book, active, onBookChange, onProgress }) {
  const last = book.page_count || 1
  const [range, setRange] = useState({ start: 1, end: last })
  const [redo, setRedo] = useState(false)
  const [job, setJob] = useState(null)
  const [error, setError] = useState('')

  // Look at the run while this tab is open. Every few seconds while it is going.
  useEffect(() => {
    if (!active) return
    let timer
    const check = () => api(`/books/${book.id}/run`).then((j) => {
      setJob(j)
      onProgress(j.done?.length ?? 0)
      if (j.running) timer = setTimeout(check, 3000)
    }, () => {})
    check()
    return () => clearTimeout(timer)
  }, [book.id, active, job?.running])

  const setKind = (paper) => api(`/books/${book.id}/kind`, { method: 'PUT', json: { kind: paper ? 'paper' : 'book' } })
    .then(onBookChange, (err) => setError(err.message))

  const start = () => {
    const pages = range.end - range.start + 1
    // Each page is one or two LLM calls. Ask before a long run.
    if (pages > 30 && !window.confirm(`This makes a summary for ${pages} pages, one Claude call or more per page. It can take a long time. Start?`)) return
    setError('')
    api(`/books/${book.id}/run`, { method: 'POST', json: { ...range, redo } }).then(setJob, (err) => setError(err.message))
  }
  const stop = () => api(`/books/${book.id}/run`, { method: 'DELETE' }).then(setJob, () => {})
  const pick = (key) => (e) => setRange((r) => ({ ...r, [key]: Math.min(Math.max(1, e.target.valueAsNumber || 1), last) }))

  const total = job?.start ? job.end - job.start + 1 : 0
  const finished = job?.start ? job.done.length + job.empty.length + job.failed.length : 0
  return (
    <section className="whole-pdf">
      <h2>Whole PDF</h2>
      <label className="check">
        <input type="checkbox" checked={book.kind === 'paper'} disabled={job?.running} onChange={(e) => setKind(e.target.checked)} />
        This is a research paper: cover every claim and number, explain what results mean, and list what to question
      </label>
      <div className="row">
        <span>Pages</span>
        <input type="number" min="1" max={last} value={range.start} onChange={pick('start')} aria-label="First page" disabled={job?.running} />
        <span>to</span>
        <input type="number" min="1" max={last} value={range.end} onChange={pick('end')} aria-label="Last page" disabled={job?.running} />
        <label className="check">
          <input type="checkbox" checked={redo} onChange={(e) => setRedo(e.target.checked)} disabled={job?.running} />
          Redo pages that already have one
        </label>
      </div>
      <div className="row">
        {job?.running
          ? <button onClick={stop} disabled={job.stop}>{job.stop ? 'Stopping after this page…' : 'Stop'}</button>
          : <button className="primary" onClick={start} disabled={range.start > range.end}>Summarize every page</button>}
        <a href={`/api/books/${book.id}/summary.md`} download>Save all summaries as .md</a>
      </div>
      {job?.start && (
        <div className="progress" role="status">
          <progress value={finished} max={total} aria-label="Pages done" />
          <span className="muted small">
            {job.running ? `Page ${job.current ?? job.start}. ` : job.stop ? 'Stopped. ' : 'Done. '}
            {finished} of {total} pages
            {job.empty.length > 0 && `, ${job.empty.length} with no text`}
            {job.failed.length > 0 && `, ${job.failed.length} failed`}.
          </span>
          {job.failed.length > 0 && (
            <p className="error small">
              Failed: pages {job.failed.map((f) => f.page).join(', ')}. Press the button again to try only the missing pages.
              {' '}({job.failed[0].error})
            </p>
          )}
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  )
}

const GUIDE_MAX_PAGES = 60 // same number as MAX_GUIDE_PAGES in app.py

// Every contents entry that holds this page, the widest first, with its first and last page.
function sectionsAt(toc, page, numPages) {
  return toc.map((entry, id) => {
    const next = toc.slice(id + 1).find((n) => n.level <= entry.level)
    // Parts and chapters (levels 1 and 2) start on a new page. A smaller section often starts in the
    // middle of a page, so the page where the next one starts is counted too. Better one page too
    // many than a missed ending.
    const last = next ? next.page - (entry.level <= 2 ? 1 : 0) : numPages
    return { ...entry, id, start: entry.page, end: Math.max(entry.page, Math.min(last, numPages)) }
  }).filter((s) => s.start <= page && page <= s.end)
}

const PAGE_VALUES = { read: 'Read it', skim: 'Skim', skip: 'Can skip', unchecked: 'Not checked' }

// A whole chapter in one guide: the key ideas, the details that are easy to miss, and a line for
// every page, so the reader can see that no page was left out.
function ChapterGuide({ book, page, toc, numPages, go, active, onAsk }) {
  const [question, setQuestion] = useState('')
  // No table of contents (most research papers): the whole document is one part.
  const sections = toc.length > 0 ? sectionsAt(toc, page, numPages || 0)
    : numPages ? [{ id: -1, level: 1, title: 'Whole document', start: 1, end: numPages }] : []
  const fits = (s) => s.end - s.start + 1 <= GUIDE_MAX_PAGES
  const [pickedId, setPickedId] = useState(null)
  const section = sections.find((s) => s.id === pickedId && fits(s)) || sections.find(fits)
  const range = section && `start=${section.start}&end=${section.end}`
  const [asked, setAsked] = useState(null) // the range the reader asked a guide for
  const [tries, setTries] = useState(0)
  const [state, setState] = useState({ loading: true })
  const fresh = useRef(false) // Regenerate: used by the next load only
  const regenerate = () => {
    fresh.current = true
    setAsked(range)
    setTries((t) => t + 1)
  }

  useEffect(() => {
    if (!active || !range) return
    let live = true
    setState({ loading: true })
    // Until the reader asks, only a saved guide is fetched. That costs nothing.
    const extra = fresh.current ? '&fresh=true' : asked === range ? '' : '&only_saved=true'
    fresh.current = false
    api(`/books/${book.id}/guide?${range}${extra}`).then(
      (guide) => live && setState({ guide }),
      (err) => live && setState({ error: err.message }),
    )
    return () => { live = false }
  }, [book.id, range, asked, tries, active])

  if (book.status !== 'ready') return <p className="muted">Reading the book for the first time…</p>
  if (toc.length === 0 && !section) {
    return (
      <p className="muted">
        This PDF has no table of contents, and it is longer than {GUIDE_MAX_PAGES} pages, so it cannot be cut into chapters.
      </p>
    )
  }
  if (!section) {
    return (
      <p className="muted">
        This chapter is longer than {GUIDE_MAX_PAGES} pages, too long for one guide.
        Go to a section inside it and open this tab again.
      </p>
    )
  }

  const count = section.end - section.start + 1
  const { guide } = state
  return (
    <div className="guide">
      {sections.length > 1 && (
        <select value={section.id} aria-label="Which part of the book" onChange={(e) => setPickedId(Number(e.target.value))}>
          {sections.map((s) => (
            <option key={s.id} value={s.id} disabled={!fits(s)}>
              {s.title} (pages {s.start} to {s.end}{fits(s) ? '' : ', too long'})
            </option>
          ))}
        </select>
      )}
      {state.loading && (asked === range
        ? <p className="muted" role="status">Reading all {count} pages. This can take a minute or two…</p>
        : <div className="skeleton" role="status" aria-label="Looking for a saved guide"><span /><span /><span /></div>)}
      {state.error && (
        <p className="error">{state.error} <button onClick={() => setTries((t) => t + 1)}>Try again</button></p>
      )}
      {guide?.not_made && (
        <div className="invite">
          <h2>{section.title}</h2>
          <button className="primary" onClick={() => setAsked(range)}>Learn this chapter quickly</button>
          <p>The tool reads all {count} pages (pages {section.start} to {section.end}) and gives you the important ideas.</p>
          <p>It also lists every page, so you can see what was kept and what you can skip.</p>
        </div>
      )}
      {guide?.ideas && (
        <>
          <p className="summary">{guide.summary}</p>
          <h2>Key ideas</h2>
          {guide.ideas.map((idea, i) => (
            <div key={i} className="idea">
              <strong>{idea.title}</strong>
              <p>{idea.explanation}</p>
              <span className="pages">{idea.pages.map((p) => <PageLink key={p} page={p} go={go} />)}</span>
            </div>
          ))}
          {guide.details.length > 0 && (
            <>
              <h2>Easy to miss</h2>
              <ul className="details">
                {guide.details.map((d, i) => (
                  <li key={i}>{d.text} {d.page && <span className="pages"><PageLink page={d.page} go={go} /></span>}</li>
                ))}
              </ul>
            </>
          )}
          <h2>Every page</h2>
          <ul className="pagemap">
            {guide.pages.map((p) => (
              <li key={p.page} className={p.value} aria-current={p.page === page ? 'true' : undefined}>
                <span className="pages"><PageLink page={p.page} go={go} /></span>
                <span><strong>{PAGE_VALUES[p.value]}</strong>{p.figure && ' (has a figure)'}. {p.note}</span>
              </li>
            ))}
          </ul>
          <p className="muted small">
            Made from the text of pages {section.start} to {section.end}, for a {guide.level} reader.
            Figures were not looked at. Their pages are marked.{' '}
            <button className="link" onClick={regenerate}>Regenerate this guide</button>
          </p>
          <form className="ask-chapter" onSubmit={(e) => {
            e.preventDefault()
            if (!question.trim()) return
            onAsk({ start: section.start, end: section.end, title: section.title }, question.trim())
            setQuestion('')
          }}>
            <h2>Ask about this chapter</h2>
            <textarea rows="2" value={question} aria-label="Your question about this chapter"
              placeholder="For example: Explain the second key idea again with a simple example."
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) e.currentTarget.form.requestSubmit() }} />
            <div className="row">
              <span className="muted small">Opens in Chat. Follow-up questions stay about pages {section.start} to {section.end}.</span>
              <button className="primary" disabled={!question.trim()}>Ask</button>
            </div>
          </form>
        </>
      )}
    </div>
  )
}

const LEVELS = ['beginner', 'learning', 'comfortable']
const skillName = (s) => s.replaceAll('_', ' ')

// The tool never moves a reader to less simple explanations alone. It asks here.
function Suggestion({ skill, onProfileChange }) {
  const { kind, level } = skill.suggest
  const answer = (yes) => {
    const call = yes
      ? api(`/skills/${skill.skill}`, { method: 'PUT', json: { level } })
      : api(`/skills/${skill.skill}/decline`, { method: 'POST' })
    call.then(onProfileChange, () => {})
  }
  return (
    <div className="suggestion">
      <p>
        {kind === 'ask_up'
          ? `You seem comfortable with ${skillName(skill.skill)} now. Stop the extra simple explanations?`
          : `${skillName(skill.skill)} seems hard lately. Go back to simpler explanations?`}
      </p>
      <button className="primary" onClick={() => answer(true)}>Yes, use “{level}”</button>{' '}
      <button onClick={() => answer(false)}>Not yet</button>
    </div>
  )
}

function Skills({ active, version, onProfileChange }) {
  const [skills, setSkills] = useState(null)

  useEffect(() => {
    if (active) api('/skills').then(setSkills, () => setSkills([]))
  }, [active, version])

  if (!skills) return null
  if (skills.length === 0) {
    return (
      <p className="muted">
        Nothing here yet. While you read, the tool learns which topics need simple explanations.
        You can also tell it in Chat, for example: “I am comfortable with calculus.”
      </p>
    )
  }
  const setLevel = (skill, level) =>
    api(`/skills/${skill}`, { method: 'PUT', json: { level } }).then(onProfileChange, () => {})

  return (
    <div className="card">
      {skills.map((s) => (
        <div key={s.skill} className="skill">
          <div className="row">
            <strong>{skillName(s.skill)}</strong>
            <select value={s.level} aria-label={`Level for ${skillName(s.skill)}`} onChange={(e) => setLevel(s.skill, e.target.value)}>
              {LEVELS.map((l) => <option key={l}>{l}</option>)}
            </select>
          </div>
          <p className="muted small">
            {s.source === 'stated' ? 'You set this level.' : 'This level is the tool’s guess.'}{' '}
            Signs of difficulty: {s.difficulty}. Signs of comfort: {s.comfort}.
            {s.recent.filter((e) => e.page_no).slice(0, 3).map((e, i) => (
              <span key={i}><br />{e.signal} on page {e.page_no} of {e.title}</span>
            ))}
          </p>
          {s.suggest?.kind.startsWith('ask') && <Suggestion skill={s} onProfileChange={onProfileChange} />}
        </div>
      ))}
    </div>
  )
}

function Chat({ book, page, go, onProfileChange, chapters, request }) {
  const [note, setNote] = useState('')
  const [chats, setChats] = useState([])
  const [chatId, setChatId] = useState(null) // null = a new chat
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [wholeBook, setWholeBook] = useState(false)
  const [newScope, setNewScope] = useState(null) // { start, end, title } for a new chat from a chapter guide
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const endRef = useRef(null)

  useEffect(() => {
    api(`/books/${book.id}/chats`).then(setChats, () => {})
  }, [book.id])

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [messages, busy])

  const open = (id) => {
    setChatId(id)
    setNewScope(null)
    setMessages([])
    setError('')
    if (id) api(`/chats/${id}/messages`).then(setMessages, (err) => setError(err.message))
  }

  // A saved chapter chat keeps its pages. A new one gets them from the chapter guide.
  const current = chats.find((c) => c.id === chatId)
  const scope = chatId ? (current?.start_page ? { start: current.start_page, end: current.end_page } : null) : newScope

  // chat: passed in when the chat was just changed and chatId is not updated yet.
  const ask = (question, scope, chat = chatId) => {
    setBusy(true)
    setError('')
    const where = !chat && scope ? { start: scope.start, end: scope.end } : {}
    api(`/books/${book.id}/ask`, { method: 'POST', json: { text: question, page, whole_book: wholeBook, chat_id: chat, ...where } })
      .then((res) => {
        setMessages((m) => [...m, ...res.messages])
        setText('')
        setNote(res.profile_note || '')
        if (res.profile_note) onProfileChange()
        if (!chat) {
          setChatId(res.chat_id)
          setChats((c) => [{ id: res.chat_id, title: question.slice(0, 60), page_no: scope?.start ?? page,
            start_page: scope?.start, end_page: scope?.end }, ...c])
        }
      }, (err) => setError(err.message))
      .finally(() => setBusy(false))
  }

  // A chat picked in the Notes tab, or a question from the chapter guide.
  useEffect(() => {
    if (!request) return
    if (request.scope) {
      open(null)
      setNewScope(request.scope)
      setText(request.question) // shown in the box while the answer is made
      ask(request.question, request.scope, null)
      return
    }
    api(`/books/${book.id}/chats`).then(setChats, () => {})
    open(request.id)
  }, [request])

  // Chats sit under the chapter they started in, in book order.
  const byChapter = chapters
    .map((c) => ({ ...c, chats: chats.filter((x) => x.page_no >= c.start && x.page_no <= c.end) }))
    .filter((c) => c.chats.length > 0)

  const send = (e) => {
    e.preventDefault()
    const question = text.trim()
    if (!question || busy) return
    ask(question, scope)
  }

  if (book.status !== 'ready') return <p className="muted">Reading the book for the first time…</p>
  if (book.index_status === 'error') return <p className="error">Could not prepare search: {book.error}</p>
  if (book.index_status !== 'ready') {
    return <p className="muted">Preparing search for this book. This takes a few minutes, one time only. You can read while you wait.</p>
  }

  return (
    <>
      <select value={chatId ?? ''} aria-label="Choose a chat" onChange={(e) => open(Number(e.target.value) || null)}>
        <option value="">New chat</option>
        {byChapter.map((c) => (
          <optgroup key={c.start} label={c.title}>
            {c.chats.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
          </optgroup>
        ))}
      </select>
      {scope && <p className="muted small scope">This chat is about pages {scope.start} to {scope.end}{scope.title && `, ${scope.title}`}.</p>}
      <div className="messages">
        {messages.length === 0 && !busy && !scope && (
          <p className="muted">Ask anything about this page or the book. For example: “What does this page mean?” or “I forgot what a token is.”</p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`message ${m.role}`}>
            <p>{m.text}</p>
            {m.source_pages.length > 0 && (
              <p className="pages muted small">
                Found on {m.source_pages.map((p) => <PageLink key={p} page={p} go={go} />)}
              </p>
            )}
          </div>
        ))}
        {note && <p className="muted small">{note}</p>}
        {busy && <p className="muted">Looking in the book…</p>}
        {error && <p className="error">{error}</p>}
        <div ref={endRef} />
      </div>
      <form onSubmit={send}>
        <textarea rows="2" value={text} aria-label="Your question"
          placeholder={scope ? `Ask about pages ${scope.start} to ${scope.end}…` : `Ask about page ${page}…`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) send(e) }} />
        <div className="row">
          {scope ? <span /> : (
            <label className="muted">
              <input type="checkbox" checked={wholeBook} onChange={(e) => setWholeBook(e.target.checked)} />
              {' '}Search the whole book, not only pages 1–{page}
            </label>
          )}
          <button className="primary" disabled={busy || !text.trim()}>Ask</button>
        </div>
      </form>
    </>
  )
}

function PageCard({ book, page, version, onProfileChange, autoRead }) {
  const [state, setState] = useState({ loading: true })
  const [level, setLevel] = useState(null) // set by Simpler / Too simple, for this page only
  const [asked, setAsked] = useState(false) // the reader pressed Explain now on this page
  // Regenerate: the next load makes the card again. A ref, so it is used once and a later
  // reload (for another reason) does not regenerate again.
  const fresh = useRef(false)
  const [regenerated, setRegenerated] = useState(0)
  const regenerate = () => {
    fresh.current = true
    setRegenerated((n) => n + 1)
  }

  useEffect(() => {
    if (book.status !== 'ready') return
    setState({ loading: true })
    let live = true
    // With auto read off, a page is sent to the LLM only after the reader asks. Until then the
    // server is asked for a saved card only, which costs nothing.
    const mayCallLLM = autoRead || asked || level
    const query = new URLSearchParams()
    if (level) query.set('level', level)
    if (!mayCallLLM) query.set('only_saved', 'true')
    if (!autoRead) query.set('ahead', 'false') // no cards for the next pages either
    if (fresh.current) query.set('fresh', 'true')
    fresh.current = false
    // With auto read on, wait a moment, so flipping fast through pages does not spend LLM calls
    // on pages nobody reads.
    const timer = setTimeout(() => {
      api(`/books/${book.id}/pages/${page}/card?${query}`).then(
        (card) => live && setState({ card }),
        (err) => live && setState({ error: err.message }),
      )
    }, autoRead && !asked && !level ? 600 : 0)
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [book.id, book.status, book.kind, page, level, version, autoRead, asked, regenerated])

  // The click is saved as evidence for the skills on this page, and the card is shown one level over.
  const react = (signal, by) => {
    api(`/books/${book.id}/pages/${page}/signal`, { method: 'POST', json: { signal } }).catch(() => {})
    setLevel(LEVELS[LEVELS.indexOf(state.card.level) + by])
  }

  // Read aloud uses the browser's own voice. No LLM cost.
  const [speaking, setSpeaking] = useState(false)
  useEffect(() => () => window.speechSynthesis?.cancel(), [])
  const speak = () => {
    window.speechSynthesis.cancel()
    if (speaking) return setSpeaking(false)
    const speech = new SpeechSynthesisUtterance(state.card.bullets.join(' '))
    speech.lang = 'en-US'
    speech.rate = 0.95
    speech.onend = speech.onerror = () => setSpeaking(false)
    window.speechSynthesis.speak(speech)
    setSpeaking(true)
  }

  if (book.status === 'parsing') return <p className="muted">Reading the book for the first time…</p>
  if (book.status === 'error') return <p className="error">Could not read this book: {book.error}</p>
  if (state.loading) {
    return <div className="skeleton" role="status" aria-label={`Preparing page ${page}`}><span /><span /><span /></div>
  }
  if (state.error) return <p className="error">{state.error}</p>

  const { card } = state
  if (card.not_made) {
    return (
      <div className="invite">
        <h2>Page {page} is not explained yet</h2>
        <button className="primary" onClick={() => setAsked(true)}>Explain now</button>
        <p>You get the page in 3 short points, and any formulas explained.</p>
        <p>Need help with one word or sentence? Select it on the page.</p>
      </div>
    )
  }
  if (card.no_text) return <p className="muted">No text found on this page. It may be a picture or a scanned page.</p>
  return (
    <div className="card">
      {card.skills.filter((s) => s.suggest?.kind.startsWith('ask')).map((s) => (
        <Suggestion key={s.skill} skill={s} onProfileChange={onProfileChange} />
      ))}
      <div className="card-tools">
        {'speechSynthesis' in window && <button onClick={speak}>{speaking ? 'Stop reading' : 'Read aloud'}</button>}
        <button disabled={card.level === 'beginner'} onClick={() => react('difficulty', -1)}>Simpler</button>
        <button disabled={card.level === 'comfortable'} onClick={() => react('comfort', 1)}>Too simple</button>
        <button onClick={regenerate} title="Explain this page again and replace the saved explanation">Regenerate</button>
      </div>
      {card.section && <p className="section muted small">{card.section}</p>}
      <ul className="points">{card.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul>
      <p className="muted small">
        Written for a reader who is {card.level === 'beginner' ? 'new to' : card.level === 'learning' ? 'learning' : 'comfortable with'}{' '}
        {card.skills.length > 0 ? card.skills.map((s) => skillName(s.skill)).join(' and ') : 'this topic'}.
      </p>
      {card.formulas.length > 0 && (
        <>
          <h2>Formulas</h2>
          <dl>
            {card.formulas.map((f, i) => (
              <div key={i}><dt><Formula latex={f.formula} /></dt><dd>{f.meaning}</dd></div>
            ))}
          </dl>
        </>
      )}
      {card.concerns?.length > 0 && (
        <>
          <h2>To question</h2>
          <ul className="concerns">{card.concerns.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </>
      )}
      <PageCheck book={book} page={page} />
    </div>
  )
}

// Two optional questions about the page. The result is saved as evidence for the page's skills.
function PageCheck({ book, page }) {
  const [check, setCheck] = useState(null)
  const [status, setStatus] = useState('')   // '' | 'loading' | an error message
  const [picked, setPicked] = useState({})   // question index -> chosen option index

  const load = () => {
    setStatus('loading')
    api(`/books/${book.id}/pages/${page}/check`).then(
      (res) => { setCheck(res); setStatus('') },
      (err) => setStatus(err.message),
    )
  }

  const pick = (qi, oi) => {
    const next = { ...picked, [qi]: oi }
    setPicked(next)
    if (Object.keys(next).length === check.questions.length) {
      const allRight = check.questions.every((q, i) => next[i] === q.correct)
      api(`/books/${book.id}/pages/${page}/signal`, { method: 'POST', json: { signal: allRight ? 'comfort' : 'difficulty' } }).catch(() => {})
    }
  }

  if (!check) {
    return (
      <p className="check-start">
        <button onClick={load} disabled={status === 'loading'}>
          {status === 'loading' ? 'Writing 2 questions…' : 'Check myself (2 questions)'}
        </button>
        {status && status !== 'loading' && <span className="error"> {status}</span>}
      </p>
    )
  }
  return (
    <>
      <h2>Check myself</h2>
      {check.questions.map((q, qi) => {
        const done = picked[qi] !== undefined
        return (
          <fieldset key={qi} className="question" disabled={done}>
            <legend>{q.question}</legend>
            {q.options.map((option, oi) => (
              <button key={oi} onClick={() => pick(qi, oi)} aria-pressed={picked[qi] === oi}
                className={done && oi === q.correct ? 'right' : done && picked[qi] === oi ? 'wrong' : ''}>
                {done && oi === q.correct && '✓ '}{done && picked[qi] === oi && oi !== q.correct && '✗ '}{option}
              </button>
            ))}
            {done && <p className="muted small">{q.why}</p>}
          </fieldset>
        )
      })}
    </>
  )
}
