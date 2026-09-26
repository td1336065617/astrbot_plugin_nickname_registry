from pathlib import Path
p = Path("/home/fsptd/文档/ChatGPT/astrbot_plugin_nickname_registry/pages/manage/app.js")
s = p.read_text(encoding="utf-8")

# 1) 辅助函数
old = "  function platformLabel(p) {"
new = '''  function shortId(value) {
    var text = String(value === null || value === undefined ? '' : value);
    return text.length > 14 ? text.slice(0, 8) + '…' + text.slice(-4) : text;
  }
  function groupCell(name, groupId) {
    var label = String(name || '').trim();
    var idText = String(groupId || '');
    return '<div>' + (label ? esc(label) : '<span class="muted">未获取群名</span>') + '</div>' +
      '<div class="muted mono" title="' + esc(idText) + '">' + esc(shortId(idText)) + '</div>';
  }
  function platformLabel(p) {'''
assert old in s, "helper anchor"
s = s.replace(old, new, 1)

# 2) 成员检索：群列
old2 = """      '<td class="mono">' + esc(r.group_id) + '</td>' +"""
new2 = """      '<td>' + groupCell(r.group_name, r.group_id) + '</td>' +"""
assert old2 in s, "members cell"
s = s.replace(old2, new2, 1)

# 3) 未关联清单：群列
old3 = """              '<td class="mono">' + esc(m.group_id) + '</td><td class="muted">' + esc(fmtTs(m.last_seen)) + '</td>' +"""
new3 = """              '<td>' + groupCell(m.group_name, m.group_id) + '</td><td class="muted">' + esc(fmtTs(m.last_seen)) + '</td>' +"""
assert old3 in s, "unlinked cell"
s = s.replace(old3, new3, 1)

# 4) 群与覆盖度：群列
old4 = """            return '<tr><td class="mono">' + esc(g.platform_id) + '</td><td class="mono">' + esc(g.group_id) + '</td>' +"""
new4 = """            return '<tr><td class="mono">' + esc(g.platform_id) + '</td><td>' + groupCell(g.group_name, g.group_id) + '</td>' +"""
assert old4 in s, "groups cell"
s = s.replace(old4, new4, 1)

# 5) 成员详情抽屉：显示群名
old5 = """        '<div class="field"><label>平台 / 群</label><div class="mono">' + esc(m.platform_id) + ' / ' + esc(m.group_id) + '</div></div>' +"""
new5 = """        '<div class="field"><label>平台 / 群</label><div>' + esc(m.platform_id) + ' / ' +
          (data.group_name ? esc(data.group_name) + ' ' : '') + '<span class="mono">' + esc(m.group_id) + '</span></div></div>' +"""
assert old5 in s, "drawer field"
s = s.replace(old5, new5, 1)

# 6) 「刷新群名」按钮
old6 = "        <h2>群与覆盖度</h2>"
new6 = """        <h2>群与覆盖度<span class="spacer"><button class="btn small secondary" id="gRefreshNames">刷新群名</button></span></h2>"""
assert old6 in s, "groups card head"
s = s.replace(old6, new6, 1)

old7 = "    $('#pAdd').onclick = async function () {"
new7 = """    var refreshNames = $('#gRefreshNames');
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
    $('#pAdd').onclick = async function () {"""
assert old7 in s, "bind anchor"
s = s.replace(old7, new7, 1)

p.write_text(s, encoding="utf-8")
print("app.js 已更新")
