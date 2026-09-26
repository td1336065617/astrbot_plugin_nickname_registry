/* 昵称ID档案馆 后台页面 */
(function () {
  'use strict';
  var A = window.AstrBotPluginPage;
  var TITLES = {
    overview: '概览', members: '成员检索', links: '身份关联',
    unlinked: '未关联清单', groups: '群与同步', data: '数据管理', settings: '设置'
  };
  var SETTINGS_SCHEMA = [
    { key: 'capture_enabled', label: '启用被动采集', type: 'bool' },
    { key: 'record_card', label: '记录群名片', type: 'bool' },
    { key: 'record_history', label: '记录改名历史', type: 'bool' },
    { key: 'auto_candidate', label: '允许自动候选', type: 'bool' },
    { key: 'self_report_enabled', label: '允许本人自报 QQ 号（仍只生成候选）', type: 'bool' },
    { key: 'flush_interval', label: '批量落库间隔（秒）', type: 'number', min: 1, max: 120, step: 1 },
    { key: 'flush_batch', label: '批量落库条数阈值', type: 'number', min: 10, max: 5000, step: 10 },
    { key: 'queue_max', label: '待写队列上限', type: 'number', min: 100, max: 100000, step: 100 },
    { key: 'history_keep', label: '每人每群历史保留条数', type: 'number', min: 1, max: 500, step: 1 },
    { key: 'retention_days', label: '改名历史保留天数（0 = 永久）', type: 'number', min: 0, max: 3650, step: 1, desc: '只影响改名历史；成员记录与身份关联不会被删除' },
    { key: 'sync_interval', label: '定时同步周期（秒，0 = 关闭）', type: 'number', min: 0, max: 86400, step: 60 },
    { key: 'sync_timeout', label: '同步超时（秒）', type: 'number', min: 3, max: 120, step: 1 },
    { key: 'extra_admins', label: '附加管理员（逗号分隔）', type: 'text' }
  ];
  var state = {
    view: 'overview', platformId: '',
    members: { items: [], total: 0, page: 1, size: 20, filters: {} },
    links: { items: [], total: 0, page: 1, size: 20, status: '' },
    unlinked: { items: [], total: 0, page: 1, size: 20 },
    groups: [], pairs: [], settings: {}
  };

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function text(v) { return v === null || v === undefined ? '' : String(v); }
  function esc(v) {
    return text(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function shortId(value) {
    var text = String(value === null || value === undefined ? '' : value);
    return text.length > 14 ? text.slice(0, 8) + '...' + text.slice(-4) : text;
  }
  function groupCell(name, groupId) {
    var label = String(name || '').trim();
    var idText = String(groupId || '');
    return '<div>' + (label ? esc(label) : '<span class="muted">未获取群名</span>') + '</div>' +
      '<div class="muted mono" title="' + esc(idText) + '">' + esc(shortId(idText)) + '</div>';
  }
  function platformLabel(p) {
    var map = { codeforces: 'Codeforces', nowcoder: '牛客', luogu: '洛谷', atcoder: 'AtCoder' };
    return map[p] || text(p);
  }
  function fmtTs(ts) {
    if (!ts) return '-';
    var d = new Date(Number(ts) * 1000);
    return isNaN(d.getTime()) ? '-' : d.toLocaleString('zh-CN', { hour12: false });
  }
  function toast(msg, type) {
    var div = document.createElement('div');
    div.className = 'toast ' + (type || 'ok');
    div.textContent = text(msg);
    $('#toasts').appendChild(div);
    setTimeout(function () { div.remove(); }, 3200);
  }
  async function apiGet(path, params) {
    try { return await A.apiGet(path, params || {}); }
    catch (e) { throw new Error((e && e.message) ? e.message : String(e)); }
  }
  async function apiPost(path, body) {
    try { return await A.apiPost(path, body || {}); }
    catch (e) { throw new Error((e && e.message) ? e.message : String(e)); }
  }
  function confirmDialog(title, message, onOk) {
    $('#modalBox').innerHTML = '<h3>' + esc(title) + '</h3><div class="muted">' + esc(message) + '</div>' +
      '<div class="modal-actions"><button class="btn ghost" id="mdCancel">取消</button>' +
      '<button class="btn danger" id="mdOk">确认</button></div>';
    $('#modal').hidden = false;
    $('#mdCancel').onclick = function () { $('#modal').hidden = true; };
    $('#mdOk').onclick = function () { $('#modal').hidden = true; onOk(); };
  }
  function promptDialog(title, fields, onOk) {
    var html = '<h3>' + esc(title) + '</h3>';
    fields.forEach(function (f) {
      html += '<div class="field"><label>' + esc(f.label) + '</label><div>' +
        '<input type="text" id="pf_' + esc(f.key) + '" value="' + esc(f.value || '') + '" placeholder="' + esc(f.placeholder || '') + '"></div></div>';
    });
    html += '<div class="modal-actions"><button class="btn ghost" id="mdCancel">取消</button>' +
      '<button class="btn" id="mdOk">确定</button></div>';
    $('#modalBox').innerHTML = html;
    $('#modal').hidden = false;
    $('#mdCancel').onclick = function () { $('#modal').hidden = true; };
    $('#mdOk').onclick = function () {
      var out = {};
      fields.forEach(function (f) { out[f.key] = $('#pf_' + f.key).value.trim(); });
      $('#modal').hidden = true;
      onOk(out);
    };
  }
  function openDrawer(title, html) {
    $('#drawerTitle').textContent = title;
    $('#drawerBody').innerHTML = html;
    $('#drawer').hidden = false;
  }

  // ---------------- 路由 ----------------
  function currentView() {
    var hash = (location.hash || '').replace('#/', '').trim();
    return TITLES[hash] ? hash : 'overview';
  }
  function setView(view) {
    $$('.view').forEach(function (sec) { sec.hidden = sec.getAttribute('data-view') !== view; });
    $$('.nav-item').forEach(function (a) { a.classList.toggle('active', a.getAttribute('data-nav') === view); });
    $('#viewTitle').textContent = TITLES[view] || '';
    document.body.classList.remove('nav-open');
    var scrim = $('#scrim'); if (scrim) scrim.hidden = true;
    if (view === 'overview') loadOverview();
    if (view === 'members') loadMembers();
    if (view === 'links') loadLinks();
    if (view === 'unlinked') loadUnlinked();
    if (view === 'groups') loadGroups();
    if (view === 'settings') loadSettings();
  }

  // ---------------- 概览 ----------------
  function stat(num, lbl) {
    return '<div class="stat"><div class="num">' + esc(num) + '</div><div class="lbl">' + esc(lbl) + '</div></div>';
  }
  function analysisTable(rows, columns, emptyText) {
    if (!rows.length) return '<div class="muted">' + esc(emptyText) + '</div>';
    var head = '<tr>' + columns.map(function (c) { return '<th>' + esc(c[0]) + '</th>'; }).join('') + '</tr>';
    var body = rows.map(function (r) {
      return '<tr>' + columns.map(function (c) { return '<td>' + esc(c[1](r)) + '</td>'; }).join('') + '</tr>';
    }).join('');
    return '<table class="table"><thead>' + head + '</thead><tbody>' + body + '</tbody></table>';
  }
  function renderAnalysis(dups, renames) {
    var dupTable = analysisTable(dups, [
      ['昵称', function (r) { return r.nickname || r.name || '-'; }],
      ['人数', function (r) { return r.count || r.c || 0; }],
      ['涉及群', function (r) { return r.groups || 0; }]
    ], '暂无同名多人（同一昵称被多人使用）');
    var renameTable = analysisTable(renames, [
      ['成员', function (r) { return r.display || r.nickname || r.user_id || '-'; }],
      ['改名次数', function (r) { return r.changes || r.count || 0; }],
      ['最近改名', function (r) { return fmtTs(r.last_change) || '-'; }]
    ], '暂无改名记录（采集到昵称/名片变化后会出现在这里）');
    $('#ovAnalysis').innerHTML =
      '<div class="muted" style="margin:4px 0">同名多人</div>' + dupTable +
      '<div class="muted" style="margin:10px 0 4px">改名排行</div>' + renameTable;
  }
  async function loadOverview() {
    try {
      var data = await apiGet('summary');
      if (!data.available) { toast('数据库不可用', 'err'); return; }
      state.platformId = text(data.platform_id);
      $('#platformId').textContent = state.platformId || '-';
      var s = data.stats || {};
      $('#ovStats').innerHTML =
        stat(s.members || 0, '成员记录') + stat(s.distinct_users || 0, '不同用户') +
        stat(s.groups || 0, '群') + stat(s.confirmed || 0, '已确认关联') +
        stat(s.candidate || 0, '候选') + stat(data.group_pairs || 0, '群映射');
      var analysis = await apiGet('analysis');
      renderAnalysis((analysis && analysis.duplicate_names) || [], (analysis && analysis.rename_rank) || []);
    } catch (e) { toast('读取概览失败：' + e.message, 'err'); }
  }

  // ---------------- 成员 ----------------
  function memberRow(r) {
    var linked = r.link_status === 'confirmed';
    return '<tr>' +
      '<td>' + esc(r.card || r.nickname || '-') + '</td>' +
      '<td>' + esc(r.nickname || '-') + '</td>' +
      '<td class="mono">' + esc(r.qq_display || '-') + '</td>' +
      '<td class="mono">' + esc(r.user_id) + '</td>' +
      '<td class="mono">' + esc(r.platform_id) + '</td>' +
      '<td>' + groupCell(r.group_name, r.group_id) + '</td>' +
      '<td>' + (r.channel === 'official' ? '<span class="badge info">官方</span>' : (r.channel === 'onebot' ? '<span class="badge warn">OneBot</span>' : '-')) + '</td>' +
      '<td>' + esc(r.source) + '</td>' +
      '<td class="muted">' + esc(fmtTs(r.last_seen)) + '</td>' +
      '<td>' + esc(r.msg_count) + '</td>' +
      '<td><button class="btn small secondary" data-detail="' + esc(r.platform_id + '|' + r.group_id + '|' + r.user_id) + '">详情</button></td>' +
      '</tr>';
  }
  function pager(host, page, size, total, onGo, idPrefix) {
    var pages = Math.max(1, Math.ceil(total / size));
    var prevId = (idPrefix || 'pg') + 'Prev';
    var nextId = (idPrefix || 'pg') + 'Next';
    host.innerHTML = '共 ' + total + ' 条 · 第 ' + page + ' / ' + pages + ' 页' +
      '<button class="btn small ghost" id="' + prevId + '"' + (page <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<button class="btn small ghost" id="' + nextId + '"' + (page >= pages ? ' disabled' : '') + '>下一页</button>';
    var p = host.querySelector('#' + prevId); if (p) p.onclick = function () { onGo(page - 1); };
    var n = host.querySelector('#' + nextId); if (n) n.onclick = function () { onGo(page + 1); };
  }
  async function loadMembers(page) {
    state.members.page = page || state.members.page;
    var f = {
      q: $('#mKeyword').value.trim(), qq: $('#mQq').value.trim(), openid: $('#mOpenid').value.trim(),
      channel: $('#mChannel').value, linked: $('#mLinked').value,
      page: String(state.members.page), size: String(state.members.size)
    };
    try {
      var data = await apiGet('members', f);
      state.members.items = data.items || [];
      state.members.total = data.total || 0;
      $('#mBody').innerHTML = state.members.items.length
        ? state.members.items.map(memberRow).join('')
        : '<tr><td colspan="11" class="muted">暂无数据</td></tr>';
      pager($('#mPager'), state.members.page, state.members.size, state.members.total, loadMembers, 'm');
    } catch (e) { toast('查询失败：' + e.message, 'err'); }
  }
  async function showDetail(key) {
    var parts = key.split('|');
    try {
      var data = await apiGet('member', { platform_id: parts[0], group_id: parts[1], user_id: parts[2] });
      var m = data.member || {};
      var link = data.link || null;
      var html = '<div class="field"><label>显示名</label><div>' + esc(m.card || m.nickname || '-') + '</div></div>' +
        '<div class="field"><label>昵称 / 群名片</label><div>' + esc(m.nickname || '-') + ' / ' + esc(m.card || '-') + '</div></div>' +
        '<div class="field"><label>openid / QQ号</label><div class="mono">' + esc(m.user_id) + ' / ' + esc(link ? link.qq : '-') + '</div></div>' +
        '<div class="field"><label>平台 / 群</label><div>' + esc(m.platform_id) + ' / ' +
          (data.group_name ? esc(data.group_name) + ' ' : '') + '<span class="mono">' + esc(m.group_id) + '</span></div></div>' +
        '<div class="field"><label>来源 / 发言数</label><div>' + esc(m.source) + ' / ' + esc(m.msg_count) + '</div></div>' +
        '<div class="field"><label>关联状态</label><div>' + (link ? esc(link.status) + '（' + esc(link.link_source) + '）' : '<span class="badge warn">未关联</span>') + '</div></div>';
      var history = data.history || [];
      html += '<h3>改名时间线</h3>';
      html += history.length
        ? '<div class="table-wrap"><table><thead><tr><th>时间</th><th>昵称</th><th>群名片</th></tr></thead><tbody>' +
          history.map(function (h) {
            return '<tr><td class="muted">' + esc(fmtTs(h.changed_at)) + '</td><td>' + esc(h.nickname) + '</td><td>' + esc(h.card) + '</td></tr>';
          }).join('') + '</tbody></table></div>'
        : '<div class="muted">暂无改名记录</div>';
      openDrawer('成员详情', html);
    } catch (e) { toast('读取详情失败：' + e.message, 'err'); }
  }

  // ---------------- 关联 ----------------
  async function loadLinks(page) {
    state.links.page = page || state.links.page;
    state.links.status = $('#lStatus').value;
    try {
      var data = await apiGet('links', { status: state.links.status, page: String(state.links.page), size: String(state.links.size) });
      var items = data.items || [];
      state.links.total = data.total || 0;
      $('#lBody').innerHTML = items.length
        ? items.map(function (l) {
            var badge = l.status === 'confirmed' ? 'ok' : (l.status === 'candidate' ? 'warn' : 'err');
            var label = l.status === 'confirmed' ? '已确认' : (l.status === 'candidate' ? '候选' : '已驳回');
            return '<tr><td class="mono">' + esc(l.qq) + '</td><td class="mono">' + esc(l.openid) + '</td>' +
              '<td class="mono">' + esc(l.platform_id) + '</td>' +
              '<td><span class="badge ' + badge + '">' + esc(label) + '</span></td>' +
              '<td>' + esc(l.link_source) + '</td><td>' + esc(l.note) + '</td>' +
              '<td class="muted">' + esc(fmtTs(l.updated_at)) + '</td>' +
              '<td><button class="btn small" data-confirm="' + esc(l.platform_id + '|' + l.openid + '|' + l.qq) + '">确认</button> ' +
              '<button class="btn small ghost" data-reject="' + esc(l.platform_id + '|' + l.openid) + '">驳回</button> ' +
              '<button class="btn small danger" data-unbind="' + esc(l.platform_id + '|' + l.openid) + '">解绑</button></td></tr>';
          }).join('')
        : '<tr><td colspan="8" class="muted">暂无关联记录</td></tr>';
      pager($('#lPager'), state.links.page, state.links.size, state.links.total, loadLinks, 'l');
    } catch (e) { toast('读取关联失败：' + e.message, 'err'); }
  }

  // ---------------- 未关联 ----------------
  async function loadUnlinked(page) {
    state.unlinked.page = page || state.unlinked.page;
    try {
      var data = await apiGet('unlinked', { page: String(state.unlinked.page), size: String(state.unlinked.size) });
      var items = data.items || [];
      state.unlinked.total = data.total || 0;
      $('#uBody').innerHTML = items.length
        ? items.map(function (m) {
            return '<tr><td>' + esc(m.nickname || '-') + '</td><td class="mono">' + esc(m.user_id) + '</td>' +
              '<td>' + groupCell(m.group_name, m.group_id) + '</td><td class="muted">' + esc(fmtTs(m.last_seen)) + '</td>' +
              '<td>' + esc(m.msg_count) + '</td>' +
              '<td><button class="btn small secondary" data-linkqq="' + esc(m.platform_id + '|' + m.user_id) + '">关联 QQ 号</button></td></tr>';
          }).join('')
        : '<tr><td colspan="6" class="muted">暂无未关联成员</td></tr>';
      pager($('#uPager'), state.unlinked.page, state.unlinked.size, state.unlinked.total, loadUnlinked, 'u');
    } catch (e) { toast('读取未关联清单失败：' + e.message, 'err'); }
  }

  // ---------------- 群与同步 ----------------
  async function loadGroups() {
    try {
      var data = await apiGet('groups');
      state.groups = data.items || [];
      $('#gBody').innerHTML = state.groups.length
        ? state.groups.map(function (g) {
            var can = g.capabilities && g.capabilities.can_sync_members;
            return '<tr><td class="mono">' + esc(g.platform_id) + '</td><td>' + groupCell(g.group_name, g.group_id) + '</td>' +
              '<td>' + (g.channel === 'onebot' ? 'OneBot' : (g.channel === 'official' ? '官方' : '-')) + '</td>' +
              '<td>' + esc(g.member_count) + '</td><td>' + esc(g.synced_count) + '</td>' +
              '<td class="muted">' + esc(g.last_sync_at ? fmtTs(g.last_sync_at) : '-') + '</td>' +
              '<td><button class="btn small ' + (can ? 'secondary' : 'ghost') + '" data-sync="' + esc(g.platform_id + '|' + g.group_id) + '"' +
              (can ? '' : ' disabled title="官方接口不提供群成员列表"') + '>' + (can ? '同步成员' : '不支持同步') + '</button></td></tr>';
          }).join('')
        : '<tr><td colspan="7" class="muted">暂无群数据</td></tr>';
      await loadPairs();
    } catch (e) { toast('读取群失败：' + e.message, 'err'); }
  }
  async function loadPairs() {
    try {
      var data = await apiGet('group-pairs');
      state.pairs = data.items || [];
      $('#pBody').innerHTML = state.pairs.length
        ? state.pairs.map(function (p) {
            return '<tr><td class="mono">' + esc(p.official_platform_id + ' / ' + p.official_group_id) + '</td>' +
              '<td class="mono">' + esc(p.onebot_platform_id + ' / ' + p.onebot_group_id) + '</td>' +
              '<td class="muted">' + esc(fmtTs(p.created_at)) + '</td>' +
              '<td><button class="btn small" data-cand="' + esc(p.official_platform_id + '|' + p.official_group_id + '|' + p.onebot_platform_id + '|' + p.onebot_group_id) + '">生成候选</button> ' +
              '<button class="btn small danger" data-delpair="' + esc(p.seq) + '">删除</button></td></tr>';
          }).join('')
        : '<tr><td colspan="4" class="muted">暂无群映射</td></tr>';
    } catch (e) { toast('读取群映射失败：' + e.message, 'err'); }
  }

  // ---------------- 设置 ----------------
  async function loadSettings() {
    try {
      state.settings = await apiGet('settings');
      $('#settingsBox').innerHTML = SETTINGS_SCHEMA.map(function (f) {
        var v = state.settings[f.key];
        if (f.type === 'bool') {
          return '<div class="field"><label>' + esc(f.label) + '</label><div><input type="checkbox" data-skey="' + f.key + '"' + (v ? ' checked' : '') + '></div></div>';
        }
        if (f.type === 'number') {
          return '<div class="field"><label>' + esc(f.label) + '</label><div><input type="number" data-skey="' + f.key + '" value="' + esc(v) + '" min="' + f.min + '" max="' + f.max + '" step="' + f.step + '"></div></div>';
        }
        var value = Array.isArray(v) ? v.join(', ') : text(v);
        return '<div class="field"><label>' + esc(f.label) + '</label><div><input type="text" data-skey="' + f.key + '" value="' + esc(value) + '"></div></div>';
      }).join('');
    } catch (e) { toast('读取设置失败：' + e.message, 'err'); }
  }
  async function saveSettings() {
    var payload = {};
    SETTINGS_SCHEMA.forEach(function (f) {
      var node = $('[data-skey="' + f.key + '"]');
      if (!node) return;
      if (f.type === 'bool') payload[f.key] = node.checked;
      else if (f.type === 'number') payload[f.key] = Number(node.value);
      else payload[f.key] = node.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean);
    });
    try {
      await apiPost('settings', { settings: payload });
      toast('设置已保存', 'ok');
    } catch (e) { toast('保存失败：' + e.message, 'err'); }
  }

  // ---------------- 事件 ----------------
  function bindEvents() {
    $('#navToggle').onclick = function () {
      var open = document.body.classList.toggle('nav-open');
      $('#scrim').hidden = !open;
    };
    $('#scrim').onclick = function () { document.body.classList.remove('nav-open'); $('#scrim').hidden = true; };
    $('#btnReload').onclick = function () { setView(currentView()); toast('已刷新'); };
    $('#ovRefresh').onclick = loadOverview;
    $('#drawerClose').onclick = function () { $('#drawer').hidden = true; };

    $('#mSearch').onclick = function () { state.members.page = 1; loadMembers(1); };
    $('#mBody').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-detail]');
      if (btn) showDetail(btn.getAttribute('data-detail'));
    });

    $('#lRefresh').onclick = function () { state.links.page = 1; loadLinks(1); };
    $('#lStatus').onchange = function () { state.links.page = 1; loadLinks(1); };
    $('#lBody').addEventListener('click', function (e) {
      var c = e.target.closest('[data-confirm]');
      if (c) {
        var p = c.getAttribute('data-confirm').split('|');
        confirmDialog('确认关联', '把 openid ' + p[1] + ' 关联到 QQ ' + p[2] + ' 吗？', async function () {
          try { await apiPost('links/confirm', { platform_id: p[0], openid: p[1], qq: p[2] }); toast('已确认', 'ok'); loadLinks(); }
          catch (err) { toast('确认失败：' + err.message, 'err'); }
        });
        return;
      }
      var r = e.target.closest('[data-reject]');
      if (r) {
        var pr = r.getAttribute('data-reject').split('|');
        apiPost('links/reject', { platform_id: pr[0], openid: pr[1] })
          .then(function () { toast('已驳回', 'ok'); loadLinks(); })
          .catch(function (err) { toast('驳回失败：' + err.message, 'err'); });
        return;
      }
      var u = e.target.closest('[data-unbind]');
      if (u) {
        var pu = u.getAttribute('data-unbind').split('|');
        confirmDialog('解除关联', '解除后该 openid 将不再关联 QQ 号，确定吗？', function () {
          apiPost('links/unbind', { platform_id: pu[0], openid: pu[1] })
            .then(function () { toast('已解绑', 'ok'); loadLinks(); })
            .catch(function (err) { toast('解绑失败：' + err.message, 'err'); });
        });
      }
    });
    $('#btnManualLink').onclick = function () {
      promptDialog('手工新增关联', [
        { key: 'qq', label: 'QQ 号', placeholder: '纯数字' },
        { key: 'platform_id', label: '平台实例' },
        { key: 'openid', label: 'openid' }
      ], async function (v) {
        try {
          await apiPost('links/confirm', v);
          toast('已建立关联', 'ok');
          loadLinks();
        } catch (e) { toast('失败：' + e.message, 'err'); }
      });
    };

    $('#uBody').addEventListener('click', function (e) {
      var b = e.target.closest('[data-linkqq]');
      if (!b) return;
      var parts = b.getAttribute('data-linkqq').split('|');
      promptDialog('关联到 QQ 号', [{ key: 'qq', label: 'QQ 号', placeholder: '纯数字' }], async function (v) {
        try {
          await apiPost('links/confirm', { platform_id: parts[0], openid: parts[1], qq: v.qq });
          toast('已关联', 'ok');
          loadUnlinked();
        } catch (err) { toast('失败：' + err.message, 'err'); }
      });
    });

    $('#gBody').addEventListener('click', function (e) {
      var b = e.target.closest('[data-sync]');
      if (!b) return;
      var parts = b.getAttribute('data-sync').split('|');
      b.disabled = true;
      apiPost('sync', { platform_id: parts[0], group_id: parts[1] })
        .then(function (r) { toast('同步完成：' + text(r && r.total) + ' 人', 'ok'); loadGroups(); })
        .catch(function (err) { toast('同步失败：' + err.message, 'err'); b.disabled = false; });
    });
    var refreshNames = $('#gRefreshNames');
    if (refreshNames) {
      refreshNames.onclick = async function () {
        refreshNames.disabled = true;
        try {
          var result = await apiPost('groups/refresh', { limit: 50 });
          toast('群名已更新 ' + text(result && result.updated) + ' 个' +
            (result && result.failed ? ('，失败 ' + text(result.failed) + ' 个') : '') +
            (result && result.skipped ? ('，还剩 ' + text(result.skipped) + ' 个未处理') : ''), 'ok');
          loadGroups();
        } catch (e) {
          toast('刷新群名失败：' + e.message, 'err');
        }
        refreshNames.disabled = false;
      };
    }
    $('#pAdd').onclick = async function () {
      var body = {
        action: 'add',
        official_platform_id: $('#pOffPid').value.trim(),
        official_group_id: $('#pOffGid').value.trim(),
        onebot_platform_id: $('#pOnePid').value.trim(),
        onebot_group_id: $('#pOneGid').value.trim()
      };
      try { await apiPost('group-pairs', body); toast('已添加配对', 'ok'); loadPairs(); }
      catch (e) { toast('添加失败：' + e.message, 'err'); }
    };
    $('#pBody').addEventListener('click', function (e) {
      var c = e.target.closest('[data-cand]');
      if (c) {
        var p = c.getAttribute('data-cand').split('|');
        apiPost('links/auto', {
          official_platform_id: p[0], official_group_id: p[1],
          onebot_platform_id: p[2], onebot_group_id: p[3]
        }).then(function (r) { toast('新增候选 ' + text(r && r.created) + ' 条', 'ok'); loadPairs(); })
          .catch(function (err) { toast('生成失败：' + err.message, 'err'); });
        return;
      }
      var d = e.target.closest('[data-delpair]');
      if (d) {
        apiPost('group-pairs', { action: 'delete', seq: Number(d.getAttribute('data-delpair')) })
          .then(function () { toast('已删除', 'ok'); loadPairs(); })
          .catch(function (err) { toast('删除失败：' + err.message, 'err'); });
      }
    });

    $('#btnExportCsv').onclick = function () { doExport('csv'); };
    $('#btnExportJson').onclick = function () { doExport('json'); };
    $('#btnImport').onclick = async function () {
      var file = $('#importFile').files[0];
      if (!file) { toast('请选择 JSON 文件', 'err'); return; }
      var payload;
      try { payload = JSON.parse(await file.text()); }
      catch (e) { toast('文件不是合法 JSON', 'err'); return; }
      try {
        var r = await apiPost('import', { payload: payload });
        var applied = (r && r.applied) || {};
        var skipped = applied.skipped || 0;
        toast('导入完成：成员 ' + (applied.members || 0) + ' / 关联 ' + (applied.links || 0) +
          ' / 历史 ' + (applied.history || 0) + (skipped ? ('（跳过 ' + skipped + ' 条 QQ 非法或为空的记录）') : ''), 'ok');
      } catch (e) { toast('导入失败：' + e.message, 'err'); }
    };
    $('#btnPurgeGroup').onclick = function () {
      var pid = $('#dgPid').value.trim(), gid = $('#dgGid').value.trim();
      if (!pid || !gid) { toast('请填写平台实例与群 ID', 'err'); return; }
      confirmDialog('删除该群成员', pid + ' / ' + gid + ' 的成员记录将被删除（不影响关联表），确定吗？', function () {
        apiPost('purge', { scope: 'group', platform_id: pid, group_id: gid })
          .then(function (r) { toast('已删除成员 ' + text(r && r.members) + ' 条', 'ok'); })
          .catch(function (e) { toast('删除失败：' + e.message, 'err'); });
      });
    };
    $('#btnPurgeMember').onclick = function () {
      var pid = $('#dmPid').value.trim(), uid = $('#dmUid').value.trim();
      if (!pid || !uid) { toast('请填写平台实例与用户 ID', 'err'); return; }
      confirmDialog('删除该成员', '将删除该成员记录并清除他的 QQ 号关联，确定吗？', function () {
        apiPost('purge', { scope: 'member', platform_id: pid, user_id: uid })
          .then(function (r) { toast('已删除成员 ' + text(r && r.members) + ' 条，关联 ' + text(r && r.links) + ' 条', 'ok'); })
          .catch(function (e) { toast('删除失败：' + e.message, 'err'); });
      });
    };
    $('#btnPurgeAll').onclick = function () {
      confirmDialog('清空全部数据', '成员、历史、关联、群同步记录都会被清空，且不可恢复，确定吗？', function () {
        apiPost('purge', { scope: 'all' })
          .then(function (r) { toast('已清空：' + JSON.stringify(r), 'ok'); })
          .catch(function (e) { toast('清空失败：' + e.message, 'err'); });
      });
    };
    $('#btnSaveSettings').onclick = saveSettings;
  }

  async function doExport(fmt) {
    try {
      var data = await apiGet('export', { format: fmt });
      var content = data && data.content;
      if (!content) { toast('导出内容为空', 'err'); return; }
      var blob = new Blob([content], { type: fmt === 'csv' ? 'text/csv;charset=utf-8' : 'application/json' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'nickname-archive-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.' + fmt;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast('已导出 ' + fmt.toUpperCase(), 'ok');
    } catch (e) { toast('导出失败：' + e.message, 'err'); }
  }

  window.addEventListener('hashchange', function () { setView(currentView()); });

  function boot() {
    bindEvents();
    A.ready().then(function () { setView(currentView()); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
