/* Company evidence, proposal drafts and optional provider accounts. */
let registryConnected = false;
let reviewChoiceLead = null;
let proposalUrl = '';

function updateRegistryConnection(connected) {
  registryConnected = connected;
  $('#dadataState').textContent = connected ? 'Ключ сохранён' : 'Не подключено';
  $('#dadataState').className = 'pill' + (connected ? ' on' : '');
  $('#dadataOff').hidden = !connected;
  $('#auditRegistrySearch').hidden = !connected;
  $('#auditRegistrySettings').hidden = connected;
}

async function saveRegistryKey(disconnect) {
  const input = $('#dadataKey');
  const feedback = $('#dadataFeedback');
  if (!disconnect && !input.value.trim()) {
    feedback.textContent = 'Вставьте свой API-ключ DaData.';
    input.focus();
    return;
  }
  $('#dadataSave').disabled = $('#dadataOff').disabled = true;
  try {
    const res = await api().audit_dadata_key(disconnect ? '' : input.value);
    feedback.textContent = res.ok ? (res.connected ? 'Ключ сохранён.' : 'DaData отключена.') : res.error;
    if (res.ok) {
      input.value = '';
      updateRegistryConnection(res.connected);
    }
  } catch (error) {
    feedback.textContent = 'Не удалось сохранить настройки.';
  } finally {
    $('#dadataSave').disabled = $('#dadataOff').disabled = false;
  }
}

async function leadAction(button, feedback, progress, work) {
  if (auditBusy) return;
  auditBusy = true;
  button.disabled = true;
  feedback.textContent = progress;
  try {
    await work();
  } catch (error) {
    feedback.textContent = 'Не удалось выполнить действие. Попробуйте ещё раз.';
  } finally {
    button.disabled = false;
    auditBusy = false;
  }
}

function registryLabel(value) {
  return {
    ACTIVE: 'Действует', LIQUIDATING: 'Ликвидируется', LIQUIDATED: 'Ликвидировано',
    BANKRUPT: 'Банкротство', REORGANIZING: 'Реорганизация'
  }[value] || '';
}

function renderRegistry(record) {
  const box = $('#auditRegistry');
  box.replaceChildren();
  if (!record.name) {
    auditLine(box, 'Статус', 'Юрлицо не подтверждено');
    return;
  }
  auditLine(box, 'Название', record.name);
  auditLine(box, 'ИНН', record.inn);
  auditLine(box, 'ОГРН', record.ogrn);
  auditLine(box, 'КПП', record.kpp);
  auditLine(box, 'Статус', registryLabel(record.status));
  auditLine(box, 'Адрес', record.address);
  auditLine(box, 'Руководитель', [record.manager, record.position].filter(Boolean).join(', '));
  auditLine(box, 'Источник', record.source);
  auditLine(box, 'Дата проверки', auditDate(record.checked_at));
}

function prepareRegistryQuery(site, rec, onlyIfEmpty = true) {
  const field = $('#auditRegistryQuery');
  if (onlyIfEmpty && field.value.trim()) return;
  const ids = [...(site.inns || []), ...(site.ogrns || [])];
  const names = site.legal_names || [];
  field.value = (ids.length === 1 ? ids[0] : (names.length === 1 ? names[0] : rec.name)) || '';
}

function renderRegistryCandidates(candidates) {
  const container = $('#auditCandidates');
  container.replaceChildren();
  for (const candidate of candidates) {
    const line = document.createElement('div');
    line.className = 'audit-match';
    const name = document.createElement('strong');
    name.textContent = candidate.name;
    const number = document.createElement('p');
    number.className = 'hint';
    number.textContent = [
      candidate.inn && 'ИНН ' + candidate.inn,
      candidate.kpp && 'КПП ' + candidate.kpp,
      candidate.branch_type === 'BRANCH' && 'Филиал',
      registryLabel(candidate.status)
    ].filter(Boolean).join(' · ');
    const address = document.createElement('p');
    address.textContent = candidate.address || 'Адрес в реестре не указан';
    const choose = document.createElement('button');
    choose.className = 'btn';
    choose.textContent = 'Подтвердить эту компанию';
    choose.onclick = () => saveRegistryRecord(choose, {
      provider: 'dadata', number: candidate.inn || candidate.ogrn, kpp: candidate.kpp
    });
    line.append(name, number, address, choose);
    container.append(line);
  }
}

function saveRegistryRecord(button, fields) {
  const feedback = $('#auditRegistryFeedback');
  return leadAction(button, feedback, 'Сохраняем реквизиты…', async () => {
    const res = await api().audit_registry_save(auditLead, fields);
    feedback.textContent = res.ok ? 'Реквизиты сохранены у лида.' : res.error;
    if (res.ok) {
      renderRegistry(res.registry);
      renderRegistryCandidates([]);
    }
  });
}

function showReviewChoices(reviews, source) {
  const box = $('#auditReviewChoices');
  box.replaceChildren();
  const hint = document.createElement('p');
  hint.className = 'hint';
  hint.textContent = 'Проверьте тексты и отметьте те, которые нужно разобрать.';
  box.append(hint);
  const checks = [];
  for (const review of reviews) {
    const label = document.createElement('label');
    label.className = 'opt audit-match';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = true;
    label.append(check, document.createTextNode(review));
    box.append(label);
    checks.push([check, review]);
  }
  const use = document.createElement('button');
  use.className = 'btn';
  use.textContent = 'Добавить отмеченные';
  use.onclick = () => {
    const selected = checks.filter(([check]) => check.checked).map(([, text]) => text);
    if (!selected.length) return;
    const draft = $('#auditInput').value.trim();
    const existingSource = $('#auditSource').value.trim();
    const separateSource = draft && existingSource && existingSource !== source;
    const combined = [...new Set([...(draft ? draft.split(/\n\s*\n/) : []), ...selected])].join('\n\n');
    if (combined.length > 24000 || combined.split(/\n\s*\n/).length > 30) {
      $('#auditStatus').textContent = 'Разбор принимает до 30 отзывов и до 24 000 символов.';
      return;
    }
    $('#auditInput').value = combined;
    $('#auditSource').value = separateSource ? 'Несколько источников · 2ГИС: ' + source : source;
    $('#auditStatus').textContent = 'Тексты добавлены. После проверки можно запустить разбор.';
    box.replaceChildren();
  };
  box.append(use);
}

const proposalFields = {
  topic: '#proposalTopic', offer: '#proposalOffer', preset: '#proposalPreset', message: '#proposalMessage',
  headline: '#proposalHeadline', intro: '#proposalIntro'
};
function proposalForm() {
  return Object.fromEntries(Object.entries(proposalFields).map(([key, id]) => [key, $(id).value]));
}

function renderProposal(draft) {
  for (const [key, id] of Object.entries(proposalFields)) {
    $(id).value = draft[key] || (key === 'preset' ? 'request' : '');
  }
  proposalUrl = draft.url || '';
  $('#proposalLink').hidden = !proposalUrl;
  $('#proposalFrame').hidden = true;
  $('#proposalFrame').removeAttribute('srcdoc');
}

function applyLeadWorkflow(rec, data) {
  reviewChoiceLead = rec;
  renderRegistry(data.registry || {});
  renderRegistryCandidates([]);
  $('#auditRegistryQuery').value = '';
  prepareRegistryQuery(data.site || {}, rec);
  for (const key of ['Number', 'Other', 'Name', 'Address', 'Manager']) $('#auditRegistry' + key).value = '';
  $('#auditRegistryFeedback').textContent = '';
  $('#auditReviewChoices').replaceChildren();
  $('#auditReviewInputs').open = !data.report?.checked_at;
  $('#auditSuggestReviews').hidden = !/^https:\/\/(?:[\w-]+\.)?2gis\.ru\/(?:[^/?#]+\/)*firm\//i.test(rec.url || '');
  $('#proposalTelegram').hidden = !rec.telegram;
  $('#proposalPublish').hidden = !VERCEL.connected;
  $('#proposalFeedback').textContent = '';
  renderProposal(data.proposal || {});

  const search = [rec.name, rec.city, rec.address].filter(Boolean).join(', ').slice(0, 240);
  if (search) {
    const google = document.createElement('button');
    google.className = 'btn';
    google.textContent = 'Найти в Google Картах';
    google.onclick = () => api().open_url('https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(search));
    $('#auditMapLinks').append(google);
  }
}

function runProposal(action, button) {
  const feedback = $('#proposalFeedback');
  return leadAction(button, feedback, 'Готовим мини-демо…', async () => {
    const res = await api().audit_proposal(auditLead, proposalForm(), action);
    if (!res.ok) {
      feedback.textContent = res.error;
      if (res.path) toast('HTML сохранён', [['Показать файл', () => api().reveal(res.path)]]);
      return;
    }
    if (res.html) {
      const frame = $('#proposalFrame');
      frame.srcdoc = res.html;
      frame.hidden = false;
      frame.scrollIntoView({block: 'nearest'});
      feedback.textContent = '';
      return;
    }
    proposalUrl = res.draft.url || '';
    $('#proposalLink').hidden = !proposalUrl;
    feedback.textContent = action === 'publish' ? 'Мини-демо опубликовано.' : 'Черновик сохранён.';
    if (action === 'export') {
      feedback.textContent = 'HTML сохранён. Его можно отправить как файл.';
      toast('Мини-демо сохранено', [['Показать файл', () => api().reveal(res.path)]]);
    }
  });
}

function mountLeadWorkflow() {
  $('#auditToProposal').onclick = () => {
    $('#auditProposal').scrollIntoView({block: 'start'});
    $('#proposalTopic').focus({preventScroll: true});
  };
  $('#dadataSave').onclick = () => saveRegistryKey(false);
  $('#dadataOff').onclick = () => saveRegistryKey(true);
  $('#auditRegistrySettings').onclick = () => { openSettings('conn'); $('#dadataKey').focus(); };
  $('#auditRegistryFns').onclick = () => {
    const query = $('#auditRegistryQuery').value.trim();
    const city = reviewChoiceLead?.city || '';
    if (query) copyText(/^\d+$/.test(query) ? query : [query, city].filter(Boolean).join(' '), true);
    api().open_url('https://pb.nalog.ru/');
  };
  $('#auditRegistrySearch').onclick = ({currentTarget: button}) => {
    const feedback = $('#auditRegistryFeedback');
    leadAction(button, feedback, 'Ищем в реестре…', async () => {
      renderRegistryCandidates([]);
      const res = await api().audit_registry_search(auditLead, $('#auditRegistryQuery').value);
      if (!res.ok) { feedback.textContent = res.error; return; }
      renderRegistryCandidates(res.candidates);
      feedback.textContent = res.candidates.length ? 'Сверьте найденную запись с этой компанией.' : 'Совпадений нет. Уточните юридическое название или проверьте через ФНС.';
    });
  };
  $('#auditRegistryManual').onclick = ({currentTarget: button}) => saveRegistryRecord(button, {
    number: $('#auditRegistryNumber').value, name: $('#auditRegistryName').value,
    address: $('#auditRegistryAddress').value, manager: $('#auditRegistryManager').value,
    other_number: $('#auditRegistryOther').value
  });
  $('#auditSuggestReviews').onclick = ({currentTarget: button}) => {
    const feedback = $('#auditStatus');
    leadAction(button, feedback, 'Ищем тексты на карточке 2ГИС…', async () => {
      const res = await api().audit_review_suggestions(auditLead);
      if (!res.ok) { feedback.textContent = res.error; return; }
      showReviewChoices(res.reviews, res.source);
      feedback.textContent = '';
    });
  };
  $('#auditUseReport').onclick = ({currentTarget: button}) => {
    const feedback = $('#proposalFeedback');
    const fields = proposalForm();
    if ((fields.offer || fields.message) && !window.confirm('Заменить текущий черновик результатом аудита?')) return;
    leadAction(button, feedback, 'Готовим черновик…', async () => {
      const res = await api().audit_proposal(auditLead, {}, 'starter');
      if (!res.ok) { feedback.textContent = res.error; return; }
      renderProposal(res.draft);
      const preview = await api().audit_proposal(auditLead, proposalForm(), 'preview');
      if (preview.ok) {
        $('#proposalFrame').srcdoc = preview.html;
        $('#proposalFrame').hidden = false;
      }
      feedback.textContent = 'Разбор перенесён. Проверьте текст и сохраните черновик.';
      $('#auditProposal').scrollIntoView({block: 'start'});
      $('#proposalOffer').focus({preventScroll: true});
    });
  };
  for (const [id, action] of [['Preview', 'preview'], ['Save', 'save'], ['Export', 'export'], ['Publish', 'publish']]) {
    $('#proposal' + id).onclick = ({currentTarget: button}) => runProposal(action, button);
  }
  const invalidatePreview = () => {
    $('#proposalFrame').hidden = true;
    $('#proposalLink').hidden = true;
    proposalUrl = '';
  };
  $('#proposalPreset').onchange = invalidatePreview;
  $('#proposalHeadline').oninput = invalidatePreview;
  $('#proposalIntro').oninput = invalidatePreview;
  $('#proposalCopy').onclick = () => {
    const message = $('#proposalMessage').value.trim();
    if (message) { copyText(message, true); toast('Черновик скопирован'); }
  };
  $('#proposalLink').onclick = () => {
    const input = $('#proposalMessage');
    if (!proposalUrl || input.value.includes(proposalUrl)) return;
    const next = [input.value.trim(), proposalUrl].filter(Boolean).join('\n');
    if (next.length > input.maxLength) {
      $('#proposalFeedback').textContent = 'Сократите сообщение, чтобы добавить ссылку.';
      return;
    }
    input.value = next;
  };
  $('#proposalTelegram').onclick = ({currentTarget: button}) => {
    const fields = proposalForm();
    const feedback = $('#proposalFeedback');
    if (!fields.message.trim()) { feedback.textContent = 'Сначала напишите сообщение.'; return; }
    leadAction(button, feedback, 'Открываем Telegram…', async () => {
      const saved = await api().audit_proposal(auditLead, fields, 'save');
      if (!saved.ok) { feedback.textContent = saved.error; return; }
      const res = await api().open_chat(reviewChoiceLead, 'telegram', '', false, fields.message);
      if (!res.ok) { feedback.textContent = res.error; return; }
      copyText(res.text, true);
      feedback.textContent = res.prefilled ? 'Черновик открыт в Telegram. Копия в буфере.' : 'Чат открыт. Вставьте сообщение из буфера.';
    });
  };
}
