import { useEffect, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/TextLayer.css'
import 'react-pdf/dist/Page/AnnotationLayer.css'

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

export default function App() {
  const [user, setUser] = useState(undefined) // undefined = still checking, null = logged out
  const [book, setBook] = useState(null)

  useEffect(() => {
    api('/me').then(setUser, () => setUser(null))
  }, [])

  if (user === undefined) return null
  if (!user) return <Login onLogin={setUser} />
  if (book) return <Reader book={book} onClose={() => setBook(null)} />
  // After "delete my account" the logout call gets a 401. The reader is logged out either way.
  const logout = () => api('/logout', { method: 'POST' }).catch(() => {}).then(() => setUser(null))
  return <Library user={user} onOpen={setBook} onLogout={logout} />
}

function Login({ onLogin }) {
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [sent, setSent] = useState(false)
  const submit = (e) => {
    e.preventDefault()
    setError('')
    // With real login on, the server sends a link by email. On a local setup it logs in at once.
    api('/login', { method: 'POST', json: { email } }).then(
      (res) => (res.sent ? setSent(true) : onLogin(res)),
      (err) => setError(err.message),
    )
  }
  if (sent) {
    return (
      <div className="center">
        <h1>Check your email</h1>
        <p className="lede">We sent a login link to {email}. Open it in this browser. It works once, for 15 minutes.</p>
        <button className="link" onClick={() => setSent(false)}>Use another email</button>
      </div>
    )
  }
  return (
    <form className="center" onSubmit={submit}>
      <h1>Reading Companion</h1>
      <p className="lede">Open a book. Get each page explained in simple words, and ask when you forget something.</p>
      <label htmlFor="email">Your email</label>
      <input id="email" type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
      <button className="primary">Start reading</button>
      {error && <p className="error">{error}</p>}
      <div className="account small"><ThemeToggle /></div>
    </form>
  )
}

// File names make poor titles: "[O'Reilly] AI Engineering_Building..." Shown cleaner, saved as is.
const cleanTitle = (title) => title.replace(/^\[[^\]]*\]\s*/, '').replaceAll('_', ' ')

function Library({ user, onOpen, onLogout }) {
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

  const removeAccount = () => {
    if (!window.confirm('Delete your account, all books, chats and your skills? This cannot be undone.')) return
    api('/me', { method: 'DELETE' }).then(onLogout, (err) => setError(err.message))
  }

  return (
    <div className="center">
      <h1>My books</h1>
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
        <span className="muted">{user.email}</span>
        <ThemeToggle />
        <button className="link" onClick={onLogout}>Log out</button>
        <a href="/api/export" download>Export my data</a>
        <button className="link danger" onClick={removeAccount}>Delete my account</button>
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
  const tops = toc.filter((e) => e.level === 1)
  const at = tops.findLastIndex((e) => e.page <= page)
  const chapter = at >= 0 && {
    title: tops[at].title, start: tops[at].page,
    end: Math.max(tops[at].page, (tops[at + 1]?.page ?? (numPages || page) + 1) - 1),
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
  const [selection, setSelection] = useState(null) // { text, x, y }
  const [note, setNote] = useState(null)           // { action, text, result?, error? }
  useEffect(() => {
    setSelection(null)
    setNote(null)
  }, [page])

  const onSelect = () => {
    const sel = window.getSelection()
    const text = sel.toString().replace(/\s+/g, ' ').trim()
    if (!text || !paneRef.current.contains(sel.anchorNode)) return setSelection(null)
    const box = sel.getRangeAt(0).getBoundingClientRect()
    setSelection({ text: text.slice(0, 2000), x: box.left + box.width / 2, y: box.top })
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
        </div>
      )}
      <main>
        {tocOpen && toc.length > 0 && <Contents toc={toc} page={page} go={goFromToc} onClose={() => showToc(false)} />}
        <section className={`pdf ${look}`} ref={paneRef} onMouseUp={() => setTimeout(onSelect)} onScroll={() => setSelection(null)}>
          <Document file={`/api/books/${book.id}/file`} onLoadSuccess={(pdf) => setNumPages(pdf.numPages)}
            loading={<p className="muted">Opening the book…</p>} error={<p className="error">Could not open this PDF.</p>}>
            {width > 0 && <Page pageNumber={page} width={Math.min(width - 32, 1000)} />}
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
            {[['page', 'Page'], ['chat', 'Chat'], ['skills', 'My skills']].map(([id, label]) => (
              <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
          {/* All stay mounted, so a chat is not lost when you look at the page card. */}
          <div hidden={tab !== 'page'}>
            {note && <SelectionNote note={note} onClose={() => setNote(null)} />}
            <PageCard key={page} book={book} page={page} version={profileVersion} onProfileChange={profileChanged} autoRead={autoRead} />
          </div>
          <div hidden={tab !== 'chat'} className="chat-tab">
            <Chat book={book} page={page} go={go} onProfileChange={profileChanged} />
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

function Chat({ book, page, go, onProfileChange }) {
  const [note, setNote] = useState('')
  const [chats, setChats] = useState([])
  const [chatId, setChatId] = useState(null) // null = a new chat
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [wholeBook, setWholeBook] = useState(false)
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
    setMessages([])
    setError('')
    if (id) api(`/chats/${id}/messages`).then(setMessages, (err) => setError(err.message))
  }

  const send = (e) => {
    e.preventDefault()
    const question = text.trim()
    if (!question || busy) return
    setBusy(true)
    setError('')
    api(`/books/${book.id}/ask`, { method: 'POST', json: { text: question, page, whole_book: wholeBook, chat_id: chatId } })
      .then((res) => {
        setMessages((m) => [...m, ...res.messages])
        setText('')
        setNote(res.profile_note || '')
        if (res.profile_note) onProfileChange()
        if (!chatId) {
          setChatId(res.chat_id)
          setChats((c) => [{ id: res.chat_id, title: question.slice(0, 60) }, ...c])
        }
      }, (err) => setError(err.message))
      .finally(() => setBusy(false))
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
        {chats.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
      </select>
      <div className="messages">
        {messages.length === 0 && !busy && (
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
        <textarea rows="2" value={text} placeholder={`Ask about page ${page}…`} aria-label="Your question"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) send(e) }} />
        <div className="row">
          <label className="muted">
            <input type="checkbox" checked={wholeBook} onChange={(e) => setWholeBook(e.target.checked)} />
            {' '}Search the whole book, not only pages 1–{page}
          </label>
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
  }, [book.id, book.status, page, level, version, autoRead, asked])

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
      </div>
      <ul className="points">{card.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul>
      <p className="muted small">
        Written for a reader who is {card.level === 'beginner' ? 'new to' : card.level === 'learning' ? 'learning' : 'comfortable with'}{' '}
        {card.skills.length > 0 ? card.skills.map((s) => skillName(s.skill)).join(' and ') : 'this topic'}.
      </p>
      {card.formulas.length > 0 && (
        <>
          <h2>Formulas</h2>
          <dl>
            {/* ponytail: raw LaTeX text. Add KaTeX when the parser gives clean LaTeX. */}
            {card.formulas.map((f, i) => (
              <div key={i}><dt><code>{f.formula}</code></dt><dd>{f.meaning}</dd></div>
            ))}
          </dl>
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
