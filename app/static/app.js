// 原生 JS：统一读取页面 data-endpoint，提交表单并渲染结果；无框架、无打包器。
(function () {
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  // 页面内分段控件切换（.segmented 或旧版 .tabs，作用域限定在同一个 .panel）
  qsa('[data-tab-target]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var group = btn.closest('.segmented, .tabs');
      if (group) qsa('button', group).forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      var target = document.getElementById(btn.getAttribute('data-tab-target'));
      if (!target) return;
      var host = btn.closest('.panel') || target.parentElement;
      qsa('.tab-panel', host).forEach(function (panel) { panel.hidden = (panel !== target); });
    });
  });

  // 统一 POST JSON
  window.postJSON = async function (url, payload) {
    var res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    });
    var data = null;
    try { data = await res.json(); } catch (e) { data = { detail: '响应不是 JSON' }; }
    if (!res.ok) {
      var msg = (data && (data.detail || data.message)) || ('请求失败：' + res.status);
      throw new Error(msg);
    }
    return data;
  };

  window.showError = function (host, err) {
    if (!host) return;
    host.hidden = false;
    host.textContent = '执行失败：' + (err && err.message ? err.message : String(err));
  };
})();

// ── 工作台：站点选择联动、执行采集（进度 + 即时摘要）、URL 规范化核对 ──
(function () {
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var siteSelect = qs('#site-select');
  if (siteSelect) {
    siteSelect.addEventListener('change', function () {
      var opt = siteSelect.options[siteSelect.selectedIndex];
      if (!opt || !opt.value) return;
      var target = qs('#target'), maxPages = qs('#max_pages'), policy = qs('#policy');
      if (target) target.value = opt.value;
      if (maxPages && opt.getAttribute('data-max-pages')) maxPages.value = opt.getAttribute('data-max-pages');
      if (policy && opt.getAttribute('data-policy')) policy.value = opt.getAttribute('data-policy');
    });
  }

  var scanForm = qs('#scan-form');
  if (scanForm) {
    var errorBox = qs('#scan-error');
    var progress = qs('#scan-progress');
    var progressText = qs('#scan-progress-text');
    var steps = qsa('#scan-progress-steps li');
    var summary = qs('#scan-summary');
    var submitButtons = qsa('button[type="submit"][form="scan-form"]');

    function setBusy(busy) {
      submitButtons.forEach(function (b) { b.disabled = busy; });
    }

    function markStep(name, state) {
      steps.forEach(function (li) {
        if (li.getAttribute('data-step') !== name) return;
        li.style.color = state === 'done' ? 'var(--ok)' : (state === 'error' ? 'var(--err)' : 'var(--accent)');
        if (state === 'done') li.textContent = '✓ ' + li.textContent.replace(/^[✓…]\s*/, '');
      });
    }

    scanForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      var payload = {
        target: qs('#target').value,
        max_pages: parseInt(qs('#max_pages').value, 10) || 10,
        policy: qs('#policy').value
      };
      errorBox.hidden = true;
      summary.hidden = true;
      progress.hidden = false;
      setBusy(true);
      steps.forEach(function (li) { li.style.color = ''; });
      markStep('submit', 'done');
      markStep('crawl', 'active');

      var started = Date.now();
      var stage = 0;
      var labels = ['crawl', 'detail', 'sitemap', 'reconcile'];
      var texts = ['顺序抓取列表页（同站过滤 + 分页边界判定）', '校验目录详情 URL（4xx/5xx/超时分类）', '递归读取 sitemapindex 与全部子文件', '对账并落库'];
      var timer = setInterval(function () {
        stage = Math.min(stage + 1, labels.length - 1);
        markStep(labels[stage], 'active');
        progressText.textContent = '执行中：' + texts[stage] + '（已用时 ' + ((Date.now() - started) / 1000).toFixed(1) + ' 秒）';
      }, 900);
      progressText.textContent = '执行中：' + texts[0];

      try {
        var data = await window.postJSON('/api/scan', payload);
        clearInterval(timer);
        labels.forEach(function (name) { markStep(name, 'done'); });
        progress.hidden = true;

        var s = data.summary;
        var metrics = [
          ['抓取页数', s.pages_fetched],
          ['终止（第几页）', '第 ' + s.stop_page + ' 页'],
          ['目录条目', s.catalog_count],
          ['sitemap 读取', s.sitemap_complete ? '完整（' + s.sitemap_files + ' 文件）' : '待核对（' + s.sitemap_files + ' 文件）'],
          ['sitemap 去重条目', s.sitemap_count],
          ['未收录', s.only_in_catalog],
          ['多出', s.only_in_sitemap],
          ['lastmod 不一致', s.lastmod_mismatch],
          ['失败合计', Object.keys(s.failures).reduce(function (acc, key) { return acc + s.failures[key]; }, 0)]
        ];
        var host = qs('#scan-metrics');
        host.innerHTML = '';
        metrics.forEach(function (pair) {
          var box = document.createElement('div');
          box.className = 'kpi';
          var label = document.createElement('div');
          label.className = 'k-label';
          label.textContent = pair[0];
          var value = document.createElement('div');
          value.className = 'k-value';
          value.textContent = pair[1];
          box.appendChild(label);
          box.appendChild(value);
          host.appendChild(box);
        });
        qs('#scan-conclusion').textContent = '终止原因：' + s.stop_reason + '。' + data.conclusion;
        qs('#scan-result-link').setAttribute('href', data.result_url);
        summary.hidden = false;
      } catch (err) {
        clearInterval(timer);
        progress.hidden = true;
        window.showError(errorBox, err);
      } finally {
        setBusy(false);
      }
    });
  }

  var normalizeForm = qs('#normalize-form');
  if (normalizeForm) {
    normalizeForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      var errorBox = qs('#normalize-error');
      var output = qs('#normalize-output');
      errorBox.hidden = true;
      output.hidden = true;
      try {
        var data = await window.postJSON('/api/normalize', {
          url: qs('#raw-url').value,
          base: qs('#base-url').value || null
        });
        qs('#normalize-result').textContent = data.normalized;
        qs('#normalize-changes').textContent = data.changes.length
          ? '依据：' + data.changes.join(' / ')
          : '依据：已是规范形式，无需变更';
        output.hidden = false;
      } catch (err) {
        window.showError(errorBox, err);
      }
    });
  }
})();

// ── 复核页：搜索 / 策略筛选 / 差异筛选 / 导出当前列表 CSV（纯前端，不新增接口） ──
(function () {
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var table = qs('#scan-table');
  if (!table) return;
  var search = qs('#review-search');
  var policy = qs('#review-policy');
  var hasDiff = qs('#review-has-diff');
  var stat = qs('#review-stat');
  var rows = qsa('tbody tr', table).filter(function (tr) { return !tr.hasAttribute('data-placeholder'); });

  function applyFilter() {
    var term = (search && search.value || '').trim().toLowerCase();
    var wantPolicy = policy && policy.value;
    var wantDiff = hasDiff && hasDiff.value;
    var shown = 0;
    rows.forEach(function (tr) {
      var text = tr.innerText.toLowerCase();
      var matches;
      if (wantDiff === 'pending') {
        matches = tr.getAttribute('data-complete') === '0';
      } else {
        matches = !wantDiff || tr.getAttribute('data-diff') === wantDiff;
      }
      var ok = (!term || text.indexOf(term) !== -1)
        && (!wantPolicy || tr.getAttribute('data-policy') === wantPolicy)
        && matches;
      tr.hidden = !ok;
      if (ok) shown += 1;
    });
    if (stat) stat.textContent = '显示 ' + shown + ' / ' + rows.length + ' 条扫描';
  }

  [search, policy, hasDiff].forEach(function (el) {
    if (!el) return;
    el.addEventListener(el.tagName === 'INPUT' ? 'input' : 'change', applyFilter);
  });
  applyFilter();

  function toCsv(tableEl) {
    var lines = [];
    qsa('tr', tableEl).forEach(function (tr) {
      if (tr.hidden || tr.hasAttribute('data-placeholder')) return;
      var cells = qsa('th, td', tr).map(function (cell) {
        return '"' + cell.innerText.replace(/\s+/g, ' ').replace(/"/g, '""').trim() + '"';
      });
      if (cells.length) lines.push(cells.join(','));
    });
    return lines.join('\n');
  }

  qsa('[data-export-csv]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var target = document.getElementById(btn.getAttribute('data-export-csv'));
      if (!target) return;
      var blob = new Blob(['\ufeff' + toCsv(target)], { type: 'text/csv;charset=utf-8' });
      var link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'crawlscope-scans.csv';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(link.href);
    });
  });
})();
