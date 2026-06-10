/* =====================================================================
 * 필터 탭 기능  —  바이마 출품목록관리 페이지
 * ---------------------------------------------------------------------
 * 통합 개념: "전체 / 출품중 / 품절 ..." 같은 시스템 탭과, 사용자가 만든
 *           필터 탭을 모두 동일한 "필터"로 취급합니다.
 *
 * 서버 API:
 *   GET    /manage/products/tabs        탭 목록
 *   POST   /manage/products/tabs        탭 생성
 *   PUT    /manage/products/tabs/{id}   탭 수정
 *   DELETE /manage/products/tabs/{id}   탭 삭제
 * ===================================================================== */
(function () {
  'use strict';

  /* ---------- 작은 유틸 ---------- */
  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[<>&"']/g, c => ({ '<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;' }[c]));
  }
  function allRows() {
    if (typeof DATA !== 'undefined' && DATA) return DATA;
    return [];
  }
  function parseDate(s) {
    if (!s) return null;
    let m = String(s).match(/(\d{4})\/(\d{2})\/(\d{2})(?:\s+(\d{1,2}):(\d{2}))?/);
    if (m) { const [, y, mo, d, h='0', mi='0'] = m; return new Date(+y, +mo - 1, +d, +h, +mi); }
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }
  function regDaysOf(s) {
    const d = parseDate(s);
    if (!d) return null;
    return Math.max(0, Math.floor((Date.now() - d.getTime()) / 86400000));
  }
  function avgOf(total, regAt) {
    if (total == null) return null;
    const days = regDaysOf(regAt);
    return (!days) ? Number(total) : Number(total) / days;
  }

  /* =====================================================================
   * 1) 필터 가능한 필드 정의
   *    type 에 따라 연산자·값 입력 방식이 자동으로 달라집니다.
   *    nodata:true  = 현재 수집 데이터가 없는 필드 (필터는 가능하나 결과 0)
   * ===================================================================== */
  var FIELDS = {
    // --- 성과 (조회·찜·장바구니) ---
    access_count:   { label:'총 조회수',          type:'number', get:r=>r.access_count },
    access_avg:     { label:'일평균 조회수',      type:'number', get:r=>avgOf(r.access_count, r.registered_at) },
    access_7d:      { label:'최근 7일 조회수',    type:'number', get:r=>r.access_7d, nodata:true },
    cart_count:     { label:'총 장바구니수',      type:'number', get:r=>r.cart_count },
    cart_avg:       { label:'일평균 장바구니수',  type:'number', get:r=>avgOf(r.cart_count, r.registered_at) },
    favorite_count: { label:'총 찜 수',           type:'number', get:r=>r.favorite_count },
    fav_avg:        { label:'일평균 찜수',        type:'number', get:r=>avgOf(r.favorite_count, r.registered_at) },
    rank_position:  { label:'인기순 순위',        type:'number', get:r=>r.rank_position, nodata:true },
    // --- 가격·마진 ---
    margin_amount_krw: { label:'기대마진 (원)',   type:'number', get:r=>r.expected_margin_krw },
    margin_rate:    { label:'마진율 (%)',         type:'number', get:r=>r.expected_margin_rate },
    price_yen:      { label:'바이마 출품가 (¥)',  type:'number', get:r=>r.price_yen },
    buyma_lowest_price:         { label:'바이마 최저가 (¥)',   type:'number', get:r=>r.buyma_lowest_price },
    available_lowest_price_jpy: { label:'출품가능 최저가 (¥)', type:'number', get:r=>r.available_lowest_price_jpy },
    // --- 등록·기간 ---
    reg_days:        { label:'등록기간 (일)',        type:'number', get:r=>regDaysOf(r.registered_at) },
    registered_at:   { label:'바이마 등록일',        type:'date',   get:r=>r.registered_at },
    expire_at:       { label:'바이마 구매기한',      type:'date',   get:r=>r.expire_at },
    price_updated_at:{ label:'최저가 업데이트 일시', type:'date',   get:r=>r.price_updated_at },
    source_updated_at:{label:'소싱처 업데이트 일시', type:'date',   get:r=>r.source_updated_at },
    // --- 경쟁·출처 ---
    source_count:   { label:'소싱처 수',            type:'number', get:r=>r.source_count },
    same_count:     { label:'동일품번 제품수',      type:'number', get:r=>r.same_count, nodata:true },
    top1_is_ours:   { label:'동일품번 인기1위 우리차지', type:'bool', get:r=>r.top1_is_ours === true, nodata:true },
    // --- 기본 정보 ---
    status:         { label:'상태', type:'enum', get:r=>r.status,
                      options:[['on_sale','출품중'],['waiting','출품대기중'],['no_lowest','최저가확보불가'],['sold_out','품절'],['unknown','확인필요']] },
    brand_name_en:  { label:'브랜드',              type:'text', get:r=>r.brand_name_en },
    name_ko:        { label:'상품명 (한국어)',     type:'text', get:r=>r.name_ko },
    name_ja:        { label:'상품명 (일본어)',     type:'text', get:r=>r.name_ja },
    model_id:       { label:'품번 (model_id)',     type:'text', get:r=>r.model_id },
    db_mismatch_reason: { label:'DB 상태 불일치 있음', type:'bool', get:r=>!!r.db_mismatch_reason },
  };
  window.FIELDS = FIELDS;

  // 필드 선택 드롭다운의 그룹 구성
  var FIELD_GROUPS = [
    { label:'성과 (조회·찜·장바구니)', keys:['access_count','access_avg','access_7d','cart_count','cart_avg','favorite_count','fav_avg','rank_position'] },
    { label:'가격·마진',               keys:['margin_amount_krw','margin_rate','price_yen','buyma_lowest_price','available_lowest_price_jpy'] },
    { label:'등록·기간',               keys:['reg_days','registered_at','expire_at','price_updated_at','source_updated_at'] },
    { label:'경쟁·출처',               keys:['source_count','same_count','top1_is_ours'] },
    { label:'기본 정보',               keys:['status','brand_name_en','name_ko','name_ja','model_id','db_mismatch_reason'] },
  ];

  // 타입별 연산자 (첫 항목이 기본값)
  var OPS = {
    number: [['lt','＜'],['lte','≤'],['eq','='],['gte','≥'],['gt','＞'],['between','범위(이상~이하)'],['isnull','비어있음'],['notnull','값있음']],
    date:   [['before','이전'],['after','이후'],['within','최근 N일 이내'],['between','기간'],['isnull','비어있음'],['notnull','값있음']],
    enum:   [['eq','='],['neq','≠']],
    text:   [['contains','포함'],['ncontains','미포함'],['eq','정확히 일치'],['isnull','비어있음']],
    bool:   [['true','예'],['false','아니오']],
  };
  // 값 입력칸 개수: 0 / 1 / 2
  function valueCount(type, op) {
    if (op === 'isnull' || op === 'notnull' || op === 'true' || op === 'false') return 0;
    if (op === 'between') return 2;
    return 1;
  }

  /* =====================================================================
   * 2) 매칭 로직  —  필터 = OR(그룹들),  그룹 = AND(조건들)
   * ===================================================================== */
  function condComplete(c) {
    var f = FIELDS[c.field]; if (!f) return false;
    var n = valueCount(f.type, c.op);
    if (n === 0) return true;
    if (n === 2) return c.value !== '' && c.value != null && c.value2 !== '' && c.value2 != null;
    return c.value !== '' && c.value != null;
  }
  function matchNumber(v, c) {
    if (c.op === 'isnull')  return v == null;
    if (c.op === 'notnull') return v != null;
    if (v == null) return false;
    var a = Number(c.value), b = Number(c.value2);
    switch (c.op) {
      case 'lt':  return v <  a;
      case 'lte': return v <= a;
      case 'eq':  return v === a;
      case 'gte': return v >= a;
      case 'gt':  return v >  a;
      case 'between': return v >= a && v <= b;
    }
    return false;
  }
  function matchDate(v, c) {
    var d = parseDate(v);
    if (c.op === 'isnull')  return d == null;
    if (c.op === 'notnull') return d != null;
    if (d == null) return false;
    if (c.op === 'within') {
      var n = Number(c.value);
      return d.getTime() >= Date.now() - n * 86400000;
    }
    var cv = parseDate(c.value), cv2 = parseDate(c.value2);
    switch (c.op) {
      case 'before':  return cv && d < cv;
      case 'after':   return cv && d > cv;
      case 'between': return cv && cv2 && d >= cv && d <= cv2;
    }
    return false;
  }
  function matchText(v, c) {
    if (c.op === 'isnull') return v == null || v === '';
    if (v == null) return c.op === 'ncontains';
    var s = String(v).toLowerCase(), q = String(c.value).toLowerCase();
    switch (c.op) {
      case 'contains':  return s.includes(q);
      case 'ncontains': return !s.includes(q);
      case 'eq':        return s === q;
    }
    return false;
  }
  function matchCond(item, c) {
    var f = FIELDS[c.field]; if (!f) return false;
    var v = f.get(item);
    switch (f.type) {
      case 'number': return matchNumber(v, c);
      case 'date':   return matchDate(v, c);
      case 'enum':   return c.op === 'eq' ? v === c.value : v !== c.value;
      case 'text':   return matchText(v, c);
      case 'bool':   return c.op === 'true' ? v === true : v === false;
    }
    return false;
  }
  // 외부(products.html 의 applyView)에서 호출
  function matchFilter(item, filter) {
    if (!filter || !filter.groups) return true;
    var groups = filter.groups
      .map(g => (g.conditions || []).filter(condComplete))
      .filter(conds => conds.length > 0);
    if (!groups.length) return true;
    return groups.some(conds => conds.every(c => matchCond(item, c)));
  }
  window.matchFilter = matchFilter;

  function countMatches(filter) {
    var rows = allRows(), n = 0;
    for (var i = 0; i < rows.length; i++) if (matchFilter(rows[i], filter)) n++;
    return n;
  }

  /* ---------- 사람이 읽는 요약 문구 ---------- */
  function condText(c) {
    var f = FIELDS[c.field]; if (!f) return '';
    var opl = (OPS[f.type].find(o => o[0] === c.op) || ['',''])[1];
    if (f.type === 'bool')   return f.label + ' = ' + opl;
    if (c.op === 'isnull')   return f.label + ' 비어있음';
    if (c.op === 'notnull')  return f.label + ' 값있음';
    if (c.op === 'between')  return f.label + ' ' + c.value + ' ~ ' + c.value2;
    if (c.op === 'within')   return f.label + ' 최근 ' + c.value + '일 이내';
    if (f.type === 'enum') {
      var o = (f.options.find(x => x[0] === c.value) || ['', c.value]);
      return f.label + ' ' + opl + ' ' + o[1];
    }
    return f.label + ' ' + opl + ' ' + c.value;
  }
  function summaryText(groups) {
    var parts = groups
      .map(g => (g.conditions || []).filter(condComplete))
      .filter(cs => cs.length)
      .map(cs => '( ' + cs.map(condText).join(' 그리고 ') + ' )');
    return parts.length ? parts.join('  또는  ') : '조건을 추가하면 여기에 요약이 표시됩니다.';
  }

  /* =====================================================================
   * 3) 탭 모델 — 시스템 탭(고정) + 사용자 탭(서버 저장)
   * ===================================================================== */
  function statusFilter(code) { return { groups: [{ conditions: [{ field:'status', op:'eq', value:code }] }] }; }
  var SYSTEM_TABS = [
    { id:'all',          name:'전체',           system:true, filter:null },
    { id:'sys_on_sale',  name:'출품중',         system:true, filter:statusFilter('on_sale') },
    { id:'sys_no_lowest',name:'최저가확보불가', system:true, filter:statusFilter('no_lowest') },
    { id:'sys_sold_out', name:'품절',           system:true, filter:statusFilter('sold_out') },
    { id:'sys_unknown',  name:'확인필요',       system:true, filter:statusFilter('unknown') },
  ];

  var TABS_URL = './tabs';
  var customTabs = [];
  var activeTabId = 'all';

  async function loadCustomTabs() {
    try {
      var resp = await fetch(TABS_URL, { cache: 'no-store' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      var data = await resp.json();
      return Array.isArray(data.tabs) ? data.tabs : [];
    } catch (e) {
      console.error('필터 탭 로드 실패:', e);
      return [];
    }
  }
  async function createCustomTab(name, filter) {
    var resp = await fetch(TABS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, filter: filter }),
    });
    if (!resp.ok) {
      var err = await resp.json().catch(() => ({}));
      throw new Error(err.error || ('HTTP ' + resp.status));
    }
    var data = await resp.json();
    return data.tab;
  }
  async function updateCustomTab(id, name, filter) {
    var resp = await fetch(TABS_URL + '/' + encodeURIComponent(id), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, filter: filter }),
    });
    if (!resp.ok) {
      var err = await resp.json().catch(() => ({}));
      throw new Error(err.error || ('HTTP ' + resp.status));
    }
    var data = await resp.json();
    return data.tab;
  }
  async function deleteCustomTabRemote(id) {
    var resp = await fetch(TABS_URL + '/' + encodeURIComponent(id), {
      method: 'DELETE',
    });
    if (!resp.ok && resp.status !== 204) {
      var err = await resp.json().catch(() => ({}));
      throw new Error(err.error || ('HTTP ' + resp.status));
    }
  }

  function tabById(id) {
    return SYSTEM_TABS.find(t => t.id === id) || customTabs.find(t => t.id === id) || SYSTEM_TABS[0];
  }
  function applyTab(id) {
    activeTabId = id;
    window.ACTIVE_FILTER = tabById(id).filter;
    if (typeof currentPage !== 'undefined') currentPage = 1;
    window.renderAll();
  }

  async function deleteTabInline(tab) {
    if (!confirm('"' + tab.name + '" 탭을 삭제할까요?')) return;
    try {
      await deleteCustomTabRemote(tab.id);
    } catch (err) {
      alert('삭제 실패: ' + (err && err.message ? err.message : err));
      return;
    }
    customTabs = customTabs.filter(function (x) { return x.id !== tab.id; });
    if (activeTabId === tab.id) applyTab('all');
    else window.renderAll();
  }

  /* =====================================================================
   * 4) 탭 줄 렌더링  (products.html 의 renderAll 이 호출)
   * ===================================================================== */
  function renderTabBar() {
    var box = document.getElementById('filters');
    box.innerHTML = '';
    var make = function (tab, isCustom) {
      var el = document.createElement('span');
      el.className = 'filter-chip' + (tab.id === activeTabId ? ' active' : '') + (isCustom ? ' custom' : '');
      el.innerHTML = esc(tab.name) +
        ' <span class="tab-count">' + countMatches(tab.filter).toLocaleString('ko-KR') + '</span>' +
        (isCustom
          ? ' <span class="tab-edit" title="이 탭 수정">✎</span>' +
            ' <span class="tab-del" title="이 탭 삭제">✕</span>'
          : '');
      el.addEventListener('click', function (e) {
        if (e.target.classList.contains('tab-edit')) { openBuilder(tab); return; }
        if (e.target.classList.contains('tab-del'))  { e.stopPropagation(); deleteTabInline(tab); return; }
        applyTab(tab.id);
      });
      box.appendChild(el);
    };
    SYSTEM_TABS.forEach(function (t) { make(t, false); });
    if (customTabs.length) {
      var div = document.createElement('span');
      div.className = 'tab-divider';
      box.appendChild(div);
    }
    customTabs.forEach(function (t) { make(t, true); });
    var add = document.createElement('span');
    add.className = 'tab-add';
    add.textContent = '＋';
    add.title = '새 필터 탭 만들기';
    add.addEventListener('click', function () { openBuilder(null); });
    box.appendChild(add);
  }
  window.renderTabBar = renderTabBar;

  /* =====================================================================
   * 5) 필터 빌더 모달
   * ===================================================================== */
  var fb = null;          // 모달 DOM 참조 모음
  var fbState = null;     // { editId, name, groups:[{conditions:[...]}] }

  function newCond() { return { field:'access_count', op:'lt', value:'', value2:'' }; }

  function buildModalDom() {
    var ov = document.createElement('div');
    ov.className = 'modal-overlay';
    ov.id = 'fb-modal';
    ov.innerHTML =
      '<div class="modal fb-card">' +
        '<span class="close" id="fb-close">×</span>' +
        '<h3 id="fb-title">＋ 새 필터 탭 만들기</h3>' +
        '<div class="fb-name">' +
          '<label for="fb-name-input">탭 이름</label>' +
          '<input id="fb-name-input" type="text" placeholder="예: 정리 1순위 후보 (조회수 낮고 오래된 상품)" maxlength="80">' +
        '</div>' +
        '<div id="fb-groups"></div>' +
        '<button type="button" id="fb-add-or" class="fb-add-or">＋ OR 그룹 추가 (조건 묶음을 하나 더)</button>' +
        '<div class="fb-summary" id="fb-summary"></div>' +
        '<div class="fb-count" id="fb-count"></div>' +
        '<div class="fb-footer">' +
          '<button type="button" id="fb-delete" class="fb-btn fb-btn-danger">탭 삭제</button>' +
          '<span style="flex:1"></span>' +
          '<button type="button" id="fb-cancel" class="fb-btn">취소</button>' +
          '<button type="button" id="fb-save" class="fb-btn fb-btn-primary">탭으로 저장</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(ov);
    fb = {
      overlay: ov,
      title:   ov.querySelector('#fb-title'),
      name:    ov.querySelector('#fb-name-input'),
      groups:  ov.querySelector('#fb-groups'),
      summary: ov.querySelector('#fb-summary'),
      count:   ov.querySelector('#fb-count'),
      del:     ov.querySelector('#fb-delete'),
      save:    ov.querySelector('#fb-save'),
    };

    ov.querySelector('#fb-close').onclick  = closeBuilder;
    ov.querySelector('#fb-cancel').onclick = closeBuilder;
    ov.addEventListener('click', function (e) { if (e.target === ov) closeBuilder(); });
    ov.querySelector('#fb-add-or').onclick = function () {
      fbState.groups.push({ conditions: [newCond()] });
      renderBuilder();
    };
    fb.save.onclick = saveBuilder;
    fb.del.onclick = deleteBuilder;
    fb.name.addEventListener('input', function () { fbState.name = fb.name.value; });

    // 조건 영역 이벤트 위임
    fb.groups.addEventListener('change', onGroupsChange);
    fb.groups.addEventListener('input',  onGroupsInput);
    fb.groups.addEventListener('click',  onGroupsClick);
  }

  function openBuilder(tab) {
    if (tab) {  // 수정 모드
      fbState = {
        editId: tab.id,
        name: tab.name,
        groups: JSON.parse(JSON.stringify(tab.filter.groups)),
      };
      fb.title.textContent = '✎ 필터 탭 수정';
      fb.del.style.display = '';
    } else {    // 신규 모드
      fbState = { editId: null, name: '', groups: [{ conditions: [newCond()] }] };
      fb.title.textContent = '＋ 새 필터 탭 만들기';
      fb.del.style.display = 'none';
    }
    fb.name.value = fbState.name;
    renderBuilder();
    fb.overlay.classList.add('open');
    fb.name.focus();
  }
  function closeBuilder() { fb.overlay.classList.remove('open'); }

  /* ---------- 조건 한 줄 HTML ---------- */
  function condRowHtml(c, gi, ci) {
    var f = FIELDS[c.field] || FIELDS.access_count;
    // 필드 select (그룹별 optgroup)
    var fieldOpts = FIELD_GROUPS.map(function (grp) {
      return '<optgroup label="' + esc(grp.label) + '">' +
        grp.keys.map(function (k) {
          var fd = FIELDS[k];
          return '<option value="' + k + '"' + (k === c.field ? ' selected' : '') + '>' +
            esc(fd.label) + (fd.nodata ? ' (수집예정)' : '') + '</option>';
        }).join('') + '</optgroup>';
    }).join('');
    // 연산자 select
    var opOpts = OPS[f.type].map(function (o) {
      return '<option value="' + o[0] + '"' + (o[0] === c.op ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
    }).join('');
    // 값 입력칸 (타입·연산자에 따라 자동 변형)
    var n = valueCount(f.type, c.op), valHtml = '';
    if (n === 0) {
      valHtml = '<span class="fb-noval">값 입력 불필요</span>';
    } else if (f.type === 'enum') {
      valHtml = '<select class="fb-sel fb-val" data-role="val" data-gi="' + gi + '" data-ci="' + ci + '">' +
        f.options.map(function (o) {
          return '<option value="' + o[0] + '"' + (o[0] === c.value ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
        }).join('') + '</select>';
    } else {
      var itype = (f.type === 'date' && c.op !== 'within') ? 'date' : (f.type === 'text' ? 'text' : 'number');
      var ph = (c.op === 'within') ? 'N일' : (f.type === 'text' ? '검색어' : '값');
      valHtml = '<input class="fb-input fb-val" type="' + itype + '" data-role="val" data-gi="' + gi + '" data-ci="' + ci +
                '" value="' + esc(c.value) + '" placeholder="' + ph + '">';
      if (n === 2) {
        valHtml += '<span class="fb-tilde">~</span>' +
          '<input class="fb-input fb-val" type="' + itype + '" data-role="val2" data-gi="' + gi + '" data-ci="' + ci +
          '" value="' + esc(c.value2) + '" placeholder="값">';
      }
    }
    var warn = (FIELDS[c.field] && FIELDS[c.field].nodata)
      ? '<span class="fb-warn" title="현재 이 항목은 수집된 데이터가 없어 결과가 0건일 수 있습니다.">⚠ 수집예정</span>' : '';
    return '<div class="fb-cond">' +
      '<select class="fb-sel fb-field" data-role="field" data-gi="' + gi + '" data-ci="' + ci + '">' + fieldOpts + '</select>' +
      '<select class="fb-sel fb-op" data-role="op" data-gi="' + gi + '" data-ci="' + ci + '">' + opOpts + '</select>' +
      valHtml + warn +
      '<span class="fb-cond-del" data-role="delcond" data-gi="' + gi + '" data-ci="' + ci + '" title="이 조건 삭제">✕</span>' +
      '</div>';
  }

  function renderBuilder() {
    fb.groups.innerHTML = fbState.groups.map(function (g, gi) {
      var conds = g.conditions.map(function (c, ci) { return condRowHtml(c, gi, ci); }).join('') ||
        '<div class="fb-empty">조건을 추가하세요.</div>';
      var rm = fbState.groups.length > 1
        ? '<span class="fb-group-del" data-role="delgroup" data-gi="' + gi + '" title="이 그룹 삭제">그룹 삭제 ✕</span>' : '';
      var head = '<div class="fb-group-head"><span>그룹 ' + (gi + 1) +
        ' <em>· 아래 조건을 모두 만족(AND)</em></span>' + rm + '</div>';
      var add = '<button type="button" class="fb-add-cond" data-role="addcond" data-gi="' + gi + '">＋ 조건 추가</button>';
      return '<div class="fb-group">' + head + conds + add + '</div>' +
        (gi < fbState.groups.length - 1 ? '<div class="fb-or-sep"><span>OR</span></div>' : '');
    }).join('');
    refreshCountSummary();
  }
  function refreshCountSummary() {
    fb.summary.innerHTML = '<strong>요약</strong> &nbsp;' + esc(summaryText(fbState.groups));
    var n = countMatches({ groups: fbState.groups });
    fb.count.innerHTML = '<span>이 조건에 맞는 상품</span><strong>' + n.toLocaleString('ko-KR') + '개</strong>';
  }

  /* ---------- 조건 영역 이벤트 ---------- */
  function onGroupsChange(e) {
    var el = e.target, role = el.dataset.role;
    if (role !== 'field' && role !== 'op' && role !== 'val' && role !== 'val2') return;
    var c = fbState.groups[+el.dataset.gi].conditions[+el.dataset.ci];
    if (role === 'field') {
      c.field = el.value;
      var t = FIELDS[c.field].type;
      c.op = OPS[t][0][0];                 // 타입 바뀌면 연산자 기본값으로
      c.value = (t === 'enum') ? FIELDS[c.field].options[0][0] : '';
      c.value2 = '';
      renderBuilder();
    } else if (role === 'op') {
      c.op = el.value;
      if (valueCount(FIELDS[c.field].type, c.op) < 2) c.value2 = '';
      renderBuilder();
    } else if (role === 'val')  { c.value  = el.value; refreshCountSummary(); }
    else if (role === 'val2')   { c.value2 = el.value; refreshCountSummary(); }
  }
  function onGroupsInput(e) {
    var el = e.target, role = el.dataset.role;
    if (role !== 'val' && role !== 'val2') return;        // 텍스트/숫자 입력 — 재렌더 없이 갱신(포커스 유지)
    var c = fbState.groups[+el.dataset.gi].conditions[+el.dataset.ci];
    if (role === 'val') c.value = el.value; else c.value2 = el.value;
    refreshCountSummary();
  }
  function onGroupsClick(e) {
    var el = e.target, role = el.dataset.role;
    if (role === 'addcond') {
      fbState.groups[+el.dataset.gi].conditions.push(newCond());
      renderBuilder();
    } else if (role === 'delcond') {
      fbState.groups[+el.dataset.gi].conditions.splice(+el.dataset.ci, 1);
      renderBuilder();
    } else if (role === 'delgroup') {
      fbState.groups.splice(+el.dataset.gi, 1);
      renderBuilder();
    }
  }

  /* ---------- 저장 / 삭제 ---------- */
  async function saveBuilder() {
    var name = (fb.name.value || '').trim();
    if (!name) { alert('탭 이름을 입력하세요.'); fb.name.focus(); return; }
    var groups = fbState.groups
      .map(function (g) { return { conditions: g.conditions.filter(condComplete) }; })
      .filter(function (g) { return g.conditions.length; });
    if (!groups.length) { alert('완성된 조건을 1개 이상 추가하세요. (값이 비어 있으면 저장되지 않습니다)'); return; }
    var filter = { groups: groups };

    fb.save.disabled = true;
    try {
      if (fbState.editId) {
        var updated = await updateCustomTab(fbState.editId, name, filter);
        var i = customTabs.findIndex(function (x) { return x.id === fbState.editId; });
        if (i >= 0) customTabs[i] = updated; else customTabs.push(updated);
      } else {
        var created = await createCustomTab(name, filter);
        customTabs.push(created);
        fbState.editId = created.id;
      }
    } catch (err) {
      alert('저장 실패: ' + (err && err.message ? err.message : err));
      fb.save.disabled = false;
      return;
    }
    fb.save.disabled = false;
    closeBuilder();
    applyTab(fbState.editId);                   // 저장한 탭을 바로 활성화
  }
  async function deleteBuilder() {
    if (!fbState.editId) return;
    if (!confirm('이 필터 탭을 삭제할까요?')) return;
    fb.del.disabled = true;
    try {
      await deleteCustomTabRemote(fbState.editId);
    } catch (err) {
      alert('삭제 실패: ' + (err && err.message ? err.message : err));
      fb.del.disabled = false;
      return;
    }
    fb.del.disabled = false;
    var deletedId = fbState.editId;
    customTabs = customTabs.filter(function (x) { return x.id !== deletedId; });
    closeBuilder();
    if (activeTabId === deletedId) applyTab('all');
    else window.renderAll();
  }

  /* =====================================================================
   * 6) 초기화 — products.html 부팅 시 호출
   * ===================================================================== */
  async function initFilterTabs() {
    buildModalDom();
    window.ACTIVE_FILTER = null;   // 처음엔 '전체'
    activeTabId = 'all';
    customTabs = await loadCustomTabs();
  }
  window.initFilterTabs = initFilterTabs;

})();
