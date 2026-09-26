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
const FACT_NAMES = { region: '지역', organization: '기관', program_name: '사업명', announcement_date: '발표일',
  application_start: '신청 시작일', application_end: '신청 종료일', eligibility: '대상', amount_or_limit: '지원금/한도',
  rate_or_interest: '지원율/금리', support_period: '지원기간', key_changes: '핵심 변경사항', application_method: '신청방법',
  benefit_type: '혜택 종류', quantity: '지원 물량', budget: '예산', residency_requirement: '거주 요건',
  selection_method: '선정 방법', required_documents: '제출 서류', exclusions: '제외 대상', contact: '문의처',
  changes_from_previous_round: '이전 공고와 변경점' }

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
  const [editing, setEditing] = useState('')
  const [publishedUrl, setPublishedUrl] = useState('')
  const [publicationStatus, setPublicationStatus] = useState('DRAFT')

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

  useEffect(() => {
    if (!item?.id || !(['PENDING', 'RUNNING'].includes(item.run?.status) || item.images?.some(image => image.status === 'RUNNING'))) return
    const timer = window.setInterval(() => request(`/contents/${encodeURIComponent(item.id)}`)
      .then(setItem).catch(e => setError(e.message)), 1500)
    return () => window.clearInterval(timer)
  }, [item?.id, item?.run?.status])

  async function openItem(id) {
    setError('')
    try {
      const saved = await request(`/contents/${encodeURIComponent(id)}`)
      setItem(saved)
      setPublishedUrl(saved.published_url || '')
      setPublicationStatus(saved.status)
      setTab('CONTENT')
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
      setPublishedUrl('')
      setPublicationStatus('DRAFT')
      setInput('')
      window.history.replaceState(null, '', `?id=${encodeURIComponent(saved.id)}`)
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  async function retryResearch() {
    if (!item) return
    setError('')
    try {
      await request(`/contents/${encodeURIComponent(item.id)}/research`, {
        method: 'POST', headers: { 'X-Money-Engine': 'local-ui' },
      })
      setItem(previous => ({ ...previous, run: { ...previous.run, status: 'RUNNING' } }))
    } catch (e) { setError(e.message) }
  }

  async function retryImage(slot) {
    setError('')
    try {
      await request(`/contents/${encodeURIComponent(item.id)}/images/${slot}/regenerate`, {
        method: 'POST', headers: { 'X-Money-Engine': 'local-ui' },
      })
      setItem(previous => ({ ...previous, images: previous.images.map(image => image.slot === slot ? { ...image, status: 'RUNNING' } : image) }))
    } catch (e) { setError(e.message) }
  }

  async function copy(value) {
    try { await navigator.clipboard.writeText(value || ''); setNotice('복사했습니다.') }
    catch { setError('복사할 수 없습니다. 브라우저 권한을 확인하세요.') }
  }

  async function editPart(part, heading = null) {
    if (!item || editing) return
    setEditing(part + (heading || ''))
    setError('')
    try {
      const updated = await request(`/contents/${encodeURIComponent(item.id)}/edit`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Money-Engine': 'local-ui' },
        body: JSON.stringify({ part, heading }),
      })
      setItem(updated)
      setNotice('선택한 부분을 수정했습니다.')
    } catch (e) { setError(e.message) }
    finally { setEditing('') }
  }

  async function savePublication() {
    setError('')
    try {
      const updated = await request(`/contents/${encodeURIComponent(item.id)}/publication`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-Money-Engine': 'local-ui' },
        body: JSON.stringify({ status: publicationStatus, published_url: publishedUrl.trim() || null }),
      })
      setItem(updated)
      setNotice('발행 상태를 저장했습니다.')
    } catch (e) { setError(e.message) }
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
          <p className="hint">소재를 저장하면 공식자료 조사부터 이미지까지 자동으로 진행됩니다. API 키는 SETTINGS에서 설정합니다.</p>
        </section>
      </>}
      {(tab === 'CREATE' || tab === 'CONTENT') && item && <section className="panel result-panel">
          <div className="section-heading"><h2>저장된 작업</h2><span className="pill">{item.status}</span></div>
          <h3>{item.title}</h3>
          <p className="muted">{new Date(item.created_at).toLocaleString('ko-KR')} · 작업 ID {item.id.slice(0, 8)}</p>
          <div className="progress"><div style={{ width: `${Math.round(item.steps.filter(s => s.status === 'COMPLETED').length / item.steps.length * 100)}%` }} /></div>
          <p className="status-line">{item.run?.status === 'RUNNING' || item.run?.status === 'PENDING' ? '콘텐츠 생성 중…' :
            item.outputs?.RESEARCH_GATE?.status === 'CONTENT_BLOCKED' ? '공식자료 확보 부족 — 본문/이미지 생성을 중단했습니다.' :
            item.outputs?.QUALITY_GATE?.decision === 'QUALITY_FAIL' ? '본문의 검증 정보가 부족해 태그/이미지 생성을 중단했습니다.' :
            item.run?.status === 'FAILED' ? '작업 중단 · 아래 단계의 안내를 확인하세요.' : '발행 패키지 준비 완료'}</p>
          {item.outputs?.RESEARCH_GATE?.status === 'CONTENT_BLOCKED' && item.run?.status !== 'RUNNING' &&
            <p className="review-note">핵심 사실 검증률 {Math.round((item.outputs.RESEARCH_GATE.coverage || 0) * 100)}% · {item.outputs.RESEARCH_GATE.reasons?.join(' · ')}</p>}
          {(item.run?.status === 'FAILED' || item.outputs?.RESEARCH_GATE?.status === 'CONTENT_BLOCKED' || item.outputs?.QUALITY_GATE?.decision === 'QUALITY_FAIL') && item.run?.status !== 'RUNNING' &&
            <button className="stop-button" onClick={retryResearch}>{item.outputs?.RESEARCH_GATE?.status === 'CONTENT_BLOCKED' ? 'Research 다시 시도' : '실패 단계부터 다시 시도'}</button>}
          <details><summary>Pipeline 단계별 상태</summary>
            <div className="steps">{item.steps.map(step => <div key={step.id} className="step">
              <span className={`dot ${step.status.toLowerCase()}`} />
              <span>{STAGE_NAMES[step.step] || step.step}</span>
              <span className="step-status">{step.error && step.status === 'COMPLETED' ? '주의' : STATUS_NAMES[step.status]}</span>
              {step.error && <span className="step-error">{step.error}</span>}
            </div>)}</div>
          </details>
          {item.research && <details open><summary>Research 결과 · {item.research.conflict ? '충돌 확인 필요' : '공식자료 대조'}</summary>
            <div className="research-facts">{Object.entries(FACT_NAMES).map(([key, label]) => {
              const fact = item.research.facts?.[key]
              return <div className="research-fact" key={key}><strong>{label}</strong><span>{fact?.value || '확인되지 않음'}</span>
                <em>{fact?.status || 'UNKNOWN'}</em>{fact?.source_url && <a href={fact.source_url} target="_blank" rel="noreferrer">근거</a>}</div>
            })}</div>
            {item.research.summary?.official_url && <p className="muted">공식자료: <a href={item.research.summary.official_url} target="_blank" rel="noreferrer">원문 열기</a></p>}
            {!!item.research.summary?.official_attachments?.length && <p className="muted">첨부자료: {item.research.summary.official_attachments.map((file, i) =>
              <span key={file.url}><a href={file.url} target="_blank" rel="noreferrer">{file.type} {i + 1}</a> ({file.status}) </span>)}</p>}
            {item.research.summary?.conflict_notes && <p className="error">{item.research.summary.conflict_notes}</p>}
            {!!item.sources?.filter(source => source.source_role !== 'REJECTED').length && <details><summary>조사 출처 {item.sources.filter(source => source.source_role !== 'REJECTED').length}개</summary>
              {item.sources.filter(source => source.source_role !== 'REJECTED').map(source => <p key={source.id} className="muted"><a href={source.url} target="_blank" rel="noreferrer">{source.title || source.url}</a>
                {' · '}{source.source_quality || 'OTHER'}{' · '}{source.source_role || 'EVIDENCE'}{' · '}{source.published_at || '날짜 미확인'}{' · '}{source.extract_status}</p>)}</details>}
            {item.outputs?.RESEARCH_DIAGNOSTICS && <details><summary>검색 진단</summary>
              <p className="muted">공식 도메인 후보: {item.outputs.RESEARCH_DIAGNOSTICS.official_domain_candidates?.join(', ') || 'OFFICIAL_DOMAIN_NOT_RESOLVED'} · 공식 도메인 탐색: {item.outputs.RESEARCH_DIAGNOSTICS.official_domain_attempted ? '실행' : '미실행'} · 첨부 탐색: {item.outputs.RESEARCH_DIAGNOSTICS.attachment_attempted ? '실행' : '미실행'} · Recovery: {item.outputs.RESEARCH_DIAGNOSTICS.recovery_used ? '실행' : '미실행'} · 제외한 출처: {item.outputs.RESEARCH_DIAGNOSTICS.rejected_total || 0}개</p>
              {item.outputs.RESEARCH_DIAGNOSTICS.failure_code && <p className="review-note">조사 상태: {item.outputs.RESEARCH_DIAGNOSTICS.failure_code}</p>}
              {item.outputs.RESEARCH_DIAGNOSTICS.extraction_failure_reason && <p className="review-note">사실 추출 오류: {item.outputs.RESEARCH_DIAGNOSTICS.extraction_failure_reason}</p>}
              {item.outputs.RESEARCH_DIAGNOSTICS.queries?.map((row, index) => <div key={index} className="diagnostic-row"><strong>{row.level} · {row.provider || 'UNKNOWN_PROVIDER'}</strong><p>{row.final_rendered_query || row.query}</p><span>결과 {row.result_count} · 채택 {row.adopted} · 제외 {row.rejected}</span>
                {row.official_domains?.length > 0 && <p className="muted">공식 도메인: {row.official_domains.join(', ')}</p>}
                {row.error && <p className="review-note">{row.error}</p>}
                {row.accepted_sources?.map((source, n) => <p className="muted" key={`accepted-${n}`}>채택: <a href={source.url} target="_blank" rel="noreferrer">{source.title || source.url}</a> · {source.domain} · {source.role} · 관련도 {source.relevance_score} · {source.adoption_reason} · {source.fetch_status} / {source.parse_status} · 사실 추출 {source.fact_extraction_executed ? '실행' : '미실행'} · 추출 {source.extracted_facts_count || 0}개 {source.extraction_failure_reason ? `· ${source.extraction_failure_reason}` : ''}</p>)}
                {row.rejection_reasons?.map((entry, n) => <p className="muted" key={n}>제외: {entry.title || '제목 없음'} · {entry.reason}</p>)}</div>)}</details>}
          </details>}
          <details><summary>저장된 원문 보기</summary><pre className="source-text">{item.input_source}</pre></details>
          {item.outputs?.FINAL_PACKAGE && <div className="package">
            <h2>EDITOR REPORT</h2>
            <div className="report-grid"><span>등급 <strong>{item.grade || '—'}</strong></span><span>발행판정 <strong>{item.publish_decision || '—'}</strong></span>
              <span>검색수요 <strong>{item.outputs.SEARCH_DEMAND?.demand_level || 'UNKNOWN'}</strong></span>
              <span>경쟁도 <strong>{item.outputs.SERP?.competition || 'UNKNOWN'}</strong></span>
              <span>Primary Keyword <strong>{item.primary_keyword || '—'}</strong></span>
              <span>공식 출처 <strong>{item.research?.summary?.official_url ? '확인' : '확인 필요'}</strong></span></div>
            <p className="muted">SERP GAP: {item.outputs.SERP?.content_gap?.join(' · ') || '자료 부족'}</p>
            {!!item.outputs.FINAL_PACKAGE.issues?.length && <p className="review-note">확인할 사항: {item.outputs.FINAL_PACKAGE.issues.join(' · ')}</p>}
            <div className="package-block"><h3>제목</h3><div className="block-actions"><button onClick={() => editPart('title')} disabled={!!editing}>다시 생성</button><button onClick={() => copy(item.title)}>복사</button></div><p>{item.title}</p></div>
            <div className="package-block"><h3>META DESCRIPTION</h3><div className="block-actions"><button onClick={() => editPart('meta')} disabled={!!editing}>다시 생성</button><button onClick={() => copy(item.meta_description)}>복사</button></div><p>{item.meta_description}</p></div>
            <div className="package-block"><h3>TISTORY BODY</h3><div className="block-actions"><button onClick={() => editPart('lead')} disabled={!!editing}>Lead 다시 생성</button><button onClick={() => copy(item.body)}>전체 복사</button></div>
              <pre className="body-text">{item.body}</pre><div className="heading-edits">{[...(item.body || '').matchAll(/^##\s+(.+)$/gm)].map(match => <button key={match[1]} disabled={!!editing} onClick={() => editPart('h2', match[1])}>{match[1]} 다시 작성</button>)}</div></div>
            <div className="package-block"><h3>TAGS</h3><div className="block-actions"><button onClick={() => editPart('tags')} disabled={!!editing}>다시 생성</button><button onClick={() => copy(item.tags?.join(', '))}>태그 복사</button></div><p>{item.tags?.join(', ')}</p></div>
            <div className="package-block"><h3>IMAGES</h3><div className="image-list">{item.images?.map(image => <div className="image-card" key={image.slot}>
              <strong>IMAGE {String(image.slot).padStart(2, '0')} · {image.role}</strong><span>{STATUS_NAMES[image.status]}</span>
              {image.status === 'COMPLETED' && <img alt={image.scene || image.role} src={`/api/contents/${item.id}/images/${image.slot}?v=${encodeURIComponent(image.updated_at)}`} />}
              <p>{image.filename || '파일 준비 중'} · {image.insert_after || '삽입 위치 준비 중'}</p>
              {image.error && <p className="review-note">{image.error}</p>}
              {image.status === 'COMPLETED' && <a href={`/api/contents/${item.id}/images/${image.slot}?download=true`} download={image.filename}>다운로드</a>}
              <button disabled={image.status === 'RUNNING'} onClick={() => retryImage(image.slot)}>다시 생성</button>
            </div>)}</div></div>
            <div className="package-block"><h3>CONTENT CLUSTER</h3><p>현재: {item.title}</p>
              {item.cluster?.EXISTING?.map(entry => <p key={entry.id}>기존 글: <a href={entry.url} target="_blank" rel="noreferrer">{entry.url}</a></p>)}
              <p className="muted">다음 콘텐츠: {item.next_content || '추천 없음'}</p>
              <p className="muted">업데이트 확인일: {item.cluster?.update_date || '공식 확인 필요'} · 확장 조건: {item.cluster?.expand_trigger || '없음'}</p></div>
            <div className="package-block"><h3>OFFICIAL SOURCES</h3>{item.sources?.filter(source => source.source_rank <= 4 && source.source_role !== 'REJECTED' && source.extract_status === 'OK').map(source =>
              <p key={source.id}><a href={source.url} target="_blank" rel="noreferrer">{source.title || source.url}</a> · {source.checked_at?.slice(0, 10)} · {source.extract_status}</p>)}</div>
            <div className="package-block"><h3>SEARCH CONSOLE WATCH</h3><p>{item.watch_keywords?.join(' · ') || '관찰 키워드 없음'}</p></div>
            <div className="package-block"><h3>POST-PUBLISH</h3><p>{item.outputs.FINAL_PACKAGE.post_publish}</p></div>
            <div className="package-block"><h3>발행 기록</h3><div className="publication-row">
              <select aria-label="콘텐츠 상태" value={publicationStatus} onChange={event => setPublicationStatus(event.target.value)}>
                {['DRAFT', 'ACTIVE', 'CLOSED', 'ARCHIVE', 'UPDATE'].map(value => <option key={value}>{value}</option>)}
              </select><input aria-label="발행 URL" placeholder="발행한 글의 URL" value={publishedUrl} onChange={event => setPublishedUrl(event.target.value)} />
              <button onClick={savePublication}>저장</button></div></div>
          </div>}
        </section>}

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
        <section className="intro"><span className="eyebrow">CONFIGURATION</span><h1>설정</h1><p>웹 조사와 콘텐츠·이미지 생성에 사용할 API 키를 설정합니다.</p></section>
        <section className="panel settings-panel"><h2>현재 구성</h2>
          <div><span>작업 저장</span><strong>SQLite · 로컬</strong></div>
          <div><span>기본 이미지</span><strong>3장 · 개별 상태 저장</strong></div>
          <div><span>Research / Image</span><strong>자동 생성 연결</strong></div>
          <div><span>OpenAI API 키</span><strong>{keyConfigured ? '저장됨' : '미설정'}</strong></div>
          <div className="key-row"><input type="password" autoComplete="off" aria-label="OpenAI API 키" placeholder="API 키 입력" value={apiKey} onChange={e => setApiKey(e.target.value)} />
            <button onClick={saveKey}>저장</button>{keyConfigured && <button onClick={clearKey}>삭제</button>}</div>
          <p className="hint">키는 Windows 사용자 계정에 연결해 암호화 저장하며 화면에 다시 표시하지 않습니다. 웹 검색과 글·이미지 생성에 사용합니다. API 사용 요금이 발생할 수 있습니다.</p>
          <button className="stop-button" onClick={stopApp}>프로그램 종료</button>
        </section>
      </>}
    </main>
  </div>
}

createRoot(document.getElementById('root')).render(<App />)
