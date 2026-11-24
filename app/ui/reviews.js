/* One lead's downloaded reviews and saved analysis. */
let reviewState = null;

function reviewText(parent, tag, text, className) {
  const el = document.createElement(tag);
  el.textContent = text;
  if (className) el.className = className;
  parent.append(el);
  return el;
}

function reviewDate(value) {
  return value ? new Date(value).toLocaleString('ru-RU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
  }) : '';
}

function reviewCountLabel(count) {
  const tail = count % 10;
  const teens = count % 100 >= 11 && count % 100 <= 14;
  const word = teens ? 'отзывов' : tail === 1 ? 'отзыв' : tail >= 2 && tail <= 4 ? 'отзыва' : 'отзывов';
  return count + ' ' + word;
}

function renderReviewScreen(state) {
  const sample = state.collection;
  const items = sample.items || [];
  const list = $('#reviewItems');
  const sources = $('#reviewSources');
  const controlsBusy = state.loading || state.analyzing;
  const connected = aiOn();
  $('#reviewCount').textContent = items.length ? reviewCountLabel(items.length) + ' в выборке' : 'Отзывы';
  $('#reviewDate').textContent = sample.checked_at && items.length
    ? 'Загружено ' + reviewDate(sample.checked_at) : '';
  $('#reviewRefresh').disabled = controlsBusy || !state.sources.length;
  $('#reviewAnalyze').hidden = !connected || !items.length;
  $('#reviewAnalyze').disabled = controlsBusy || !items.length;
  $('#reviewAiSettings').hidden = connected || !items.length;
  $('#reviewAiMode').textContent = connected || !items.length ? '' : 'Анализ в программе использует ваш ИИ-ключ.';
  $('#reviewPrompt').disabled = controlsBusy || !items.length;
  $('#reviewApply').disabled = controlsBusy || !items.length;
  $('#reviewAnswer').disabled = controlsBusy || !items.length;
  $('#reviewImport').hidden = !items.length;
  sources.replaceChildren();
  for (const source of state.sources) {
    const line = document.createElement('div');
    line.className = 'review-source';
    reviewText(line, 'strong', source.name);
    if (source.count != null) reviewText(line, 'span', reviewCountLabel(source.count), 'hint');
    if (source.error) reviewText(line, 'span', source.error, 'hint');
    if (/^https:\/\//.test(source.url || '')) {
      const link = reviewText(line, 'button', 'Открыть карточку', 'linkbtn');
      link.type = 'button';
      link.onclick = () => api().open_url(source.url);
    }
    sources.append(line);
  }
  list.replaceChildren();
  if (!items.length) {
    reviewText(list, 'p', state.loading ? 'Загружаем тексты с карт…' : 'В этой карточке пока нет текстов.', 'hint');
  }
  const visible = state.showAll ? items : items.slice(0, 5);
  for (const [index, item] of visible.entries()) {
    const row = document.createElement('article');
    row.className = 'review-item';
    const summary = [String(index + 1), (item.sources || []).join(', '), item.rating && String(item.rating) + ' из 5']
      .filter(Boolean).join(' · ');
    reviewText(row, 'div', summary, 'review-kind');
    reviewText(row, 'p', item.text);
    list.append(row);
  }
  const more = $('#reviewShowMore');
  more.hidden = items.length <= 5;
  more.textContent = state.showAll ? 'Свернуть список' : 'Показать все ' + items.length;
  renderReviewReport(state);
}

function renderReviewReport(state) {
  const report = state.report;
  const target = $('#reviewReport');
  target.replaceChildren();
  if (!report.checked_at) return;
  const old = report.sample_id !== state.collection.id || !state.collection.id;
  reviewText(target, 'p', (old ? 'Прошлый разбор' : 'Разбор выборки') + ' · ' + reviewDate(report.checked_at), 'hint');
  if (report.summary) reviewText(target, 'p', report.summary);
  const items = report.reviews || [];
  for (const topic of report.topics || []) {
    const ids = (topic.reviews || []).filter(id => Number.isInteger(id) && items[id - 1]);
    if (!ids.length) continue;
    const details = document.createElement('details');
    details.className = 'review-evidence';
    const kind = {problem: 'Проблема', strength: 'Сильная сторона', neutral: 'Наблюдение'}[topic.kind] || 'Тема';
    const amount = ids.length === 1 ? '1 отзыв' : String(ids.length) + ' из ' + items.length;
    reviewText(details, 'summary', kind + ' · ' + topic.title + ' · ' + amount);
    for (const id of ids) reviewText(details, 'p', '№ ' + id + ' · ' + items[id - 1]);
    target.append(details);
  }
  if (!report.summary && !report.topics?.length) reviewText(target, 'p', 'Общих тем в выборке нет.', 'hint');
}

async function openAudit(rec, trigger) {
  const state = {lid: rec.id, trigger, collection: {}, report: {}, sources: [], loading: true, analyzing: false, showAll: false};
  reviewState = state;
  $('#auditTitle').textContent = 'Отзывы · ' + (rec.name || 'Компания');
  $('#reviewLoadStatus').textContent = '';
  $('#reviewAnalysisStatus').textContent = '';
  $('#reviewAnswer').value = '';
  $('#reviewImport').open = false;
  $('#auditModal').hidden = false;
  $('#auditModal .ed').scrollTop = 0;
  $('#auditClose').focus();
  renderReviewScreen(state);
  try {
    const res = await api().audit_open(rec.id);
    if (reviewState !== state) return;
    if (!res.ok) { $('#reviewLoadStatus').textContent = res.error; return; }
    state.collection = res.collection || {};
    state.report = res.report || {};
    state.sources = state.collection.sources || res.sources || [];
    state.loading = false;
    if (!state.collection.items?.length && state.sources.length) {
      await loadReviewTexts(state);
    } else if (!state.sources.length) {
      $('#reviewLoadStatus').textContent = 'Для этого лида нет ссылки на карточку 2ГИС или Яндекс Карт.';
    }
  } catch (error) {
    if (reviewState === state) $('#reviewLoadStatus').textContent = 'Не удалось открыть отзывы.';
  } finally {
    if (reviewState === state) {
      state.loading = false;
      renderReviewScreen(state);
    }
  }
}

async function loadReviewTexts(state) {
  if (state.loading || state.analyzing) return;
  state.loading = true;
  $('#reviewLoadStatus').textContent = 'Подтягиваем отзывы с карт…';
  renderReviewScreen(state);
  try {
    const res = await api().audit_collect_reviews(state.lid);
    if (reviewState !== state) return;
    if (!res.ok) { $('#reviewLoadStatus').textContent = res.error; return; }
    const incoming = res.collection || {};
    if (state.collection.id !== incoming.id) {
      $('#reviewAnswer').value = '';
      $('#reviewAnalysisStatus').textContent = '';
    }
    state.collection = incoming;
    state.sources = res.sources || incoming.sources || [];
    state.showAll = false;
    $('#reviewLoadStatus').textContent = res.error || '';
  } catch (error) {
    if (reviewState === state) $('#reviewLoadStatus').textContent = 'Не удалось обновить отзывы. Попробуйте ещё раз.';
  } finally {
    if (reviewState === state) {
      state.loading = false;
      renderReviewScreen(state);
    }
  }
}

async function analyzeReviews(mode) {
  const state = reviewState;
  if (!state || state.loading || state.analyzing || !state.collection.items?.length) return;
  const answer = $('#reviewAnswer').value.trim();
  if (mode === 'import' && !answer) {
    $('#reviewAnalysisStatus').textContent = 'Вставьте ответ из внешнего чата.';
    return;
  }
  state.analyzing = true;
  $('#reviewAnalysisStatus').textContent = mode === 'ai' ? 'ИИ читает отзывы…' : '';
  renderReviewScreen(state);
  try {
    const res = await api().audit_reviews(state.lid, mode, answer, state.collection.id);
    if (reviewState !== state) return;
    if (!res.ok) { $('#reviewAnalysisStatus').textContent = res.error; return; }
    if (mode === 'prompt') {
      copyText(res.prompt, true);
      $('#reviewAnalysisStatus').textContent = 'Промпт с отзывами скопирован.';
    } else {
      state.report = res.report;
      $('#reviewAnalysisStatus').textContent = 'Разбор сохранён.';
    }
  } catch (error) {
    if (reviewState === state) $('#reviewAnalysisStatus').textContent = 'Не удалось разобрать отзывы. Попробуйте ещё раз.';
  } finally {
    if (reviewState === state) {
      state.analyzing = false;
      renderReviewScreen(state);
    }
  }
}

function mountReviewWorkflow() {
  $('#auditClose').onclick = () => {
    const trigger = reviewState?.trigger;
    reviewState = null;
    $('#auditModal').hidden = true;
    trigger?.focus();
  };
  $('#auditModal').addEventListener('keydown', event => {
    if (event.key === 'Escape') $('#auditClose').click();
  });
  $('#reviewRefresh').onclick = () => { if (reviewState) loadReviewTexts(reviewState); };
  $('#reviewShowMore').onclick = () => {
    if (!reviewState) return;
    reviewState.showAll = !reviewState.showAll;
    renderReviewScreen(reviewState);
  };
  $('#reviewAiSettings').onclick = () => openSettings('conn');
  $('#reviewAnalyze').onclick = () => analyzeReviews('ai');
  $('#reviewPrompt').onclick = () => analyzeReviews('prompt');
  $('#reviewApply').onclick = () => analyzeReviews('import');
}
