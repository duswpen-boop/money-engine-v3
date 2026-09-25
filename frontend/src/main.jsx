import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

const STAGE_NAMES = {
  INPUT: '입력 저장', PARSE: '소재 분석', VERIFY: '사실 검증',
  DEEP_SOURCE: '공식자료', SEARCH_DEMAND: '검색수요', SERP: '검색결과',
  SEARCH_INTENT: '검색의도', DUPLICATE_CHECK: '중복 확인',
  KEYWORD_MAP: '키워드', VALUE_ADD: '추가 가치', WRITE: '본문',
  QUALITY_GATE: '품질 검수', TAG: '태그', IMAGE: '이미지',
  FINAL_SANITIZE: '최종 정리', FINAL_PACKAGE: '발행 패키지',
}

const STATUS_NAMES = { PENDING: '대기', RUNNING: '진행 중', COMPLETED: '완료', FAILED: '실패' }

async function request(path, options) {
  const response = await fetch(`/api${path}`, options)
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '요청을 처리하지 못했습니다.')
  return data
}

function App() {
  const [tab, setTab] = useState('CREATE')
  const [input, setInput] = useState('')
  const [item, setItem] = useState(null)
  const [items, setItems] = useState([])
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [keyConfigured, setKeyConfigured] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('id')
    if (id) openItem(id)
  }, [])

  useEffect(() => {
    if (tab !== 'CONTENT') return
    request(`/contents?q=${encodeURIComponent(query)}`).then(setItems).catch(e => setError(e.message))
  }, [tab, query])

  useEffect(() => {
    if (tab === 'SETTINGS') request('/settings').then(data => setKeyConfigured(data.openai_key_configured)).catch(e => setError(e.message))
  }, [tab])

  async function openItem(id) {
    setError('')
    try {
      const saved = await request(`/contents/${encodeURIComponent(id)}`)
      setItem(saved)
      setTab('CREATE')
      window.history.replaceState(null, '', `?id=${encodeURIComponent(id)}`)
    } catch (e) { setError(e.message) }
  }

  async function create() {
    if (!input.trim()) { setError('소재를 입력해 주세요.'); return }
    setBusy(true)
    setError('')
    try {
      const saved = await request('/contents', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input_source: input }),
      })
      setItem(saved)
      setInput('')
      window.history.replaceState(null, '', `?id=${encodeURIComponent(saved.id)}`)
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  function changeTab(next) {
    setTab(next)
    setError('')
    setNotice('')
    if (next !== 'CREATE') window.history.replaceState(null, '', window.location.pathname)
  }

  async function saveKey() {
    if (!apiKey.trim()) return setError('API 키를 입력해 주세요.')
    setError('')
    try {
      const result = await request('/settings/openai-key', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Money-Engine': 'local-ui' },
        body: JSON.stringify({ value: apiKey.trim() }) })
      setKeyConfigured(result.openai_key_configured)
      setApiKey('')
      setNotice('API 키를 Windows 사용자 계정에 연결해 암호화 저장했습니다.')
    } catch (e) { setError(e.message) }
  }

  async function clearKey() {
    setError('')
    try {
      const result = await request('/settings/openai-key', { method: 'DELETE', headers: { 'X-Money-Engine': 'local-ui' } })
      setKeyConfigured(result.openai_key_configured)
      setNotice('저장된 API 키를 삭제했습니다.')
    } catch (e) { setError(e.message) }
  }

  async function stopApp() {
    if (!window.confirm('Money Engine을 종료할까요?')) return
    setError('')
    try {
      await request('/system/stop', { method: 'POST', headers: { 'X-Money-Engine': 'local-ui' } })
      setNotice('프로그램이 종료되었습니다. 다시 사용하려면 MoneyEngine.exe를 더블클릭하세요.')
    } catch (e) { setError(e.message) }
  }

  return <div className="app">
    <header className="topbar">
      <div className="brand"><span className="brand-mark">M</span><span>MONEY ENGINE <small>V3.0</small></span></div>
      <nav aria-label="주 메뉴">
        {['CREATE', 'CONTENT', 'SETTINGS'].map(name =>
          <button key={name} className={tab === name ? 'selected' : ''} onClick={() => changeTab(name)}>{name}</button>
        )}
      </nav>
    </header>

    <main>
      {error && <div className="error" role="alert">{error}</div>}
      {notice && <div className="notice" role="status">{notice}</div>}
      {tab === 'CREATE' && <>
        <section className="intro">
          <span className="eyebrow">CONTENT WORKSPACE</span>
          <h1>소재 하나에서, 완성된 콘텐츠까지.</h1>
          <p>실시간 소재, 공고문, URL 또는 메모를 그대로 붙여넣으세요.</p>
        </section>
        <section className="panel input-panel">
          <div className="section-heading"><h2>소재 입력</h2><span>형식 자유</span></div>
          <textarea aria-label="소재 입력" value={input} onChange={e => setInput(e.target.value)}
            placeholder={'[A급 신규]\n보은군 전기차 구매보조금 28대 추가\n10월 6일부터 신청...'} />
          <div className="actions"><button className="primary" disabled={busy} onClick={create}>{busy ? '작업 저장 중…' : '분석 및 콘텐츠 생성'}</button></div>
          <p className="hint">PHASE 1: 입력과 작업 상태를 저장합니다. 실제 조사와 생성은 다음 단계에서 연결됩니다.</p>
        </section>
        {item && <section className="panel result-panel">
          <div className="section-heading"><h2>저장된 작업</h2><span className="pill">{item.status}</span></div>
          <h3>{item.title}</h3>
          <p className="muted">{new Date(item.created_at).toLocaleString('ko-KR')} · 작업 ID {item.id.slice(0, 8)}</p>
          <div className="progress"><div style={{ width: `${Math.round(item.steps.filter(s => s.status === 'COMPLETED').length / item.steps.length * 100)}%` }} /></div>
          <p className="status-line">입력 저장 완료 · 조사 및 콘텐츠 생성 대기</p>
          <details><summary>Pipeline 단계별 상태</summary>
            <div className="steps">{item.steps.map(step => <div key={step.id} className="step">
              <span className={`dot ${step.status.toLowerCase()}`} />
              <span>{STAGE_NAMES[step.step] || step.step}</span>
              <span className="step-status">{STATUS_NAMES[step.status]}</span>
            </div>)}</div>
          </details>
          <details><summary>저장된 원문 보기</summary><pre className="source-text">{item.input_source}</pre></details>
          <p className="hint">이미지 {item.images.length}개가 독립 작업으로 준비되었습니다. 현재 상태: 대기.</p>
        </section>}
      </>}

      {tab === 'CONTENT' && <>
        <section className="intro"><span className="eyebrow">SAVED CONTENT</span><h1>콘텐츠 목록</h1><p>저장된 소재와 작업 상태를 확인하고 다시 열 수 있습니다.</p></section>
        <section className="panel list-panel">
          <input aria-label="콘텐츠 검색" className="search" placeholder="제목 또는 소재 검색" value={query} onChange={e => setQuery(e.target.value)} />
          {items.length ? <div className="list">{items.map(row =>
            <button className="list-row" key={row.id} onClick={() => openItem(row.id)}>
              <span><strong>{row.title}</strong><small>{new Date(row.created_at).toLocaleString('ko-KR')}</small></span>
              <span className="pill">{row.status}</span>
            </button>
          )}</div> : <p className="empty">저장된 콘텐츠가 없습니다.</p>}
        </section>
      </>}

      {tab === 'SETTINGS' && <>
        <section className="intro"><span className="eyebrow">CONFIGURATION</span><h1>설정</h1><p>외부 서비스 연결은 해당 단계에서 추가합니다.</p></section>
        <section className="panel settings-panel"><h2>현재 구성</h2>
          <div><span>작업 저장</span><strong>SQLite · 로컬</strong></div>
          <div><span>기본 이미지</span><strong>3장 · 개별 상태 저장</strong></div>
          <div><span>Research / Image</span><strong>연결 대기</strong></div>
          <div><span>OpenAI API 키</span><strong>{keyConfigured ? '저장됨' : '미설정'}</strong></div>
          <div className="key-row"><input type="password" autoComplete="off" aria-label="OpenAI API 키" placeholder="API 키 입력" value={apiKey} onChange={e => setApiKey(e.target.value)} />
            <button onClick={saveKey}>저장</button>{keyConfigured && <button onClick={clearKey}>삭제</button>}</div>
          <p className="hint">키는 Windows 사용자 계정에 연결해 암호화 저장하며 화면에 다시 표시하지 않습니다. PHASE 1에서는 아직 사용하지 않습니다.</p>
          <button className="stop-button" onClick={stopApp}>프로그램 종료</button>
        </section>
      </>}
    </main>
  </div>
}

createRoot(document.getElementById('root')).render(<App />)
