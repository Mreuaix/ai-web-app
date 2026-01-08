(function () {
  const keywordEl = document.getElementById('keyword');
  const limitEl = document.getElementById('limit');
  const btnStart = document.getElementById('btnStart');
  const statusEl = document.getElementById('streamStatus');
  const cardsEl = document.getElementById('cards');
  const toggles = Array.from(document.querySelectorAll('.toggle__input'));

  let stream = null;

  function defaultCover() {
    return '/static/img/default-cover.svg';
  }

  async function saveOne(id, btn) {
    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = '保存中…';
    try {
      const r = await fetch('/api/items/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_ids: [id] })
      });
      const d = await r.json();
      if (!d.ok) {
        btn.disabled = false;
        btn.textContent = oldText;
        alert(d.error || '保存失败');
        return;
      }
      btn.textContent = '已保存';
      btn.classList.add('is-saved');
    } catch (e) {
      btn.disabled = false;
      btn.textContent = oldText;
      alert('保存失败');
    }
  }

  function card(item) {
    const el = document.createElement('div');
    el.className = 'cardItem';
    const cover = item.cover_url || defaultCover();
    const saved = !!item.saved;
    el.innerHTML = `
      <div class="cardItem__cover"><img src="${cover}" alt="" onerror="this.src='${defaultCover()}'" /></div>
      <div class="cardItem__body">
        <div class="cardItem__title">
          <a href="${item.url}" target="_blank" rel="noopener noreferrer">${item.title}</a>
        </div>
        <div class="cardItem__meta">
          <span>${item.published_at ? item.published_at.slice(0, 10) : item.collected_at.slice(0, 10)}</span>
          <span class="badge badge--muted">${item.source}</span>
        </div>
      </div>
      <button class="cardItem__save btn btn--ghost btn--sm ${saved ? 'is-saved' : ''}" type="button" ${saved ? 'disabled' : ''}>${saved ? '已保存' : '保存'}</button>
    `;
    const btn = el.querySelector('.cardItem__save');
    btn.addEventListener('click', () => saveOne(item.id, btn));
    return el;
  }

  async function start() {
    const keyword = (keywordEl.value || '').trim();
    if (!keyword) {
      alert('请输入关键字');
      return;
    }
    const limitRaw = Number(limitEl ? limitEl.value : 10);
    const limit = Math.max(1, Math.min(50, Number.isFinite(limitRaw) ? Math.floor(limitRaw) : 10));
    const sourceIds = toggles.filter(x => x.checked).map(x => Number(x.value));
    if (sourceIds.length === 0) {
      alert('请选择至少一个爬虫源');
      return;
    }
    cardsEl.innerHTML = '';
    statusEl.textContent = '启动采集中…';

    const r = await fetch('/api/collect/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword, source_ids: sourceIds, limit })
    });
    const d = await r.json();
    if (!d.ok) {
      alert(d.error || '启动失败');
      return;
    }
    if (stream) stream.close();
    stream = new EventSource(`/api/collect/stream/${d.channel_id}`);
    stream.addEventListener('ready', () => {
      statusEl.textContent = '采集中…（数据将实时渲染）';
    });
    stream.addEventListener('status', (e) => {
      const data = JSON.parse(e.data || '{}');
      if (data.status === 'running') statusEl.textContent = '采集中…';
      if (data.status === 'done') statusEl.textContent = '采集完成';
      if (data.status === 'empty') statusEl.textContent = data.message || '未获取到数据';
    });
    stream.addEventListener('item', (e) => {
      const data = JSON.parse(e.data || '{}');
      if (!data.item) return;
      cardsEl.appendChild(card(data.item));
      cardsEl.scrollTop = cardsEl.scrollHeight;
    });
    stream.addEventListener('close', () => {
      stream.close();
    });
    stream.onerror = () => {
      statusEl.textContent = '连接异常，已停止';
      stream.close();
    };
  }

  btnStart.addEventListener('click', start);
  keywordEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') start();
  });
})();
