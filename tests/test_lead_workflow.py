import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app import audit, reviews
from app.api import Api
from app.base import LeadBase
from app.tg import draft_link


LEAD = {
    'name': 'Блеск', 'url': 'https://2gis.ru/moscow/firm/70000001022723330',
    'yandex': 'https://yandex.ru/maps/org/123456789',
    'telegram': 'https://t.me/studio_name',
}
TWO_GIS = """
<div class="_49x36f">Служебный текст в другом месте</div>
<div class="_1k5soqfl">
  <div class="_49x36f">Очень <span>долго</span> ждали цену.</div>
  <div class="_49x36f">Спасибо за отзыв! Администратор.</div>
</div>
<div class="_1k5soqfl"><div class="_49x36f">Мастер всё объяснил.</div></div>
"""
YANDEX = """
<div class="business-review-view">
  <span class="business-review-view__body-text">очень долго ждали цену</span>
  <span class="business-review-view__comment">Ответ владельца</span>
</div>
<div class="business-review-view">
  <span class="business-review-view__body-text">Работу сделали аккуратно.</span>
</div>
<script type="application/json">
{"reviews":[{"id":"r1","rating":4,"text":"Работу сделали аккуратно."}],
 "info":{"text":"Описание компании без отзыва."}}
</script>
"""


class ReviewCollectionTests(TestCase):
    def test_sources_are_company_cards_and_redirects_keep_id(self):
        sources = reviews.sources_for(LEAD)
        self.assertEqual(sources[0]['url'], 'https://2gis.ru/moscow/firm/70000001022723330/tab/reviews')
        self.assertEqual(sources[1]['url'], 'https://yandex.ru/maps/org/123456789/reviews/')
        self.assertTrue(reviews._same_card(
            sources[1]['url'], 'https://yandex.com/maps/org/blest/123456789/reviews/', reviews.MAP_YANDEX,
        ))
        self.assertFalse(reviews._same_card(
            sources[1]['url'], 'https://yandex.ru/maps/org/blest/567890123/reviews/', reviews.MAP_YANDEX,
        ))
        self.assertEqual(reviews.sources_for({
            'url': 'https://2gis.ru.example/firm/12345678',
            'yandex': 'https://yandex.ru/maps/?text=Блеск',
        }), [])

    def test_only_reads_texts_not_business_replies_or_chrome(self):
        data = reviews.extract_html(TWO_GIS)
        self.assertEqual([r['text'] for r in data], ['Очень долго ждали цену.', 'Мастер всё объяснил.'])
        data = reviews.extract_html(YANDEX)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[1]['rating'], 4)

    def test_collect_merges_a_review_found_on_both_maps(self):
        with patch('app.reviews._download', side_effect=lambda src: reviews.extract_html(
            TWO_GIS if src['name'] == reviews.MAP_2GIS else YANDEX
        )):
            result = reviews.collect(LEAD)
        self.assertEqual(len(result['items']), 3)
        self.assertEqual(result['items'][0]['sources'], [reviews.MAP_2GIS, reviews.MAP_YANDEX])
        self.assertEqual([s['count'] for s in result['sources']], [2, 2])
        self.assertEqual(len(result['id']), 64)

    def test_partial_error_keeps_available_reviews(self):
        def download(source):
            if source['name'] == reviews.MAP_2GIS:
                raise reviews.Unavailable('Карта запросила проверку в браузере.')
            return reviews.extract_html(YANDEX)
        with patch('app.reviews._download', side_effect=download):
            result = reviews.collect(LEAD)
        self.assertEqual(len(result['items']), 2)
        self.assertTrue(result['sources'][0]['error'])
        self.assertEqual(result['items'][0]['sources'], [reviews.MAP_YANDEX])

    def test_uses_browser_for_dynamic_public_html(self):
        with patch('app.reviews._download', return_value=[]), patch(
            'app.reviews._browser_reviews', return_value=reviews.extract_html(TWO_GIS)
        ) as browse:
            result = reviews.collect({'url': LEAD['url']}, 'chrome.exe')
        browse.assert_called_once()
        self.assertEqual(len(result['items']), 2)


class LeadReviewTests(TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.app = Api.__new__(Api)
        self.app._settings = {}
        self.app._review_guard = Lock()
        self.app._reviews_fetching = set()
        self.app._base = LeadBase(Path(folder) / 'leads.db')
        self.addCleanup(self.app._base.close)
        self.lead = self.app._base.add(LEAD)
        self.id = self.lead['id']
        self.collection = {
            'id': 'one', 'sources': [], 'checked_at': '2026-11-25',
            'items': [{'text': 'Долго ждали цену.', 'sources': ['2ГИС'], 'rating': 3}],
        }

    def test_auto_collection_is_saved_and_used_for_prompt_and_ai(self):
        with patch('app.reviews.collect', return_value=self.collection):
            self.assertTrue(self.app.audit_collect_reviews(self.id)['ok'])
        self.assertIn('Долго ждали цену.', self.app.audit_reviews(self.id, 'prompt', sample_id='one')['prompt'])
        self.assertFalse(self.app.audit_reviews(self.id, 'ai', sample_id='one')['ok'])
        self.app._settings['ai'] = {'key': 'buyer-test-key'}
        with patch('app.ai.complete', return_value=json.dumps({
            'summary': 'Один клиент долго ждал цену.',
            'topics': [{'kind': 'problem', 'title': 'Ожидание', 'reviews': [1, 9]}],
        })) as complete:
            response = self.app.audit_reviews(self.id, 'ai', sample_id='one')
        self.assertTrue(response['ok'])
        self.assertEqual(response['report']['topics'][0]['reviews'], [1])
        self.assertEqual(complete.call_args.args[0]['key'], 'buyer-test-key')
        self.assertEqual(self.app.audit_open(self.id)['report']['sample_id'], 'one')

    def test_source_failure_preserves_stored_reviews_and_prevents_stale_import(self):
        self.app._base.save_review_collection(self.id, self.collection)
        with patch('app.reviews.collect', return_value={'sources': [], 'items': []}):
            res = self.app.audit_collect_reviews(self.id)
        self.assertEqual(res['collection']['id'], 'one')
        self.assertFalse(self.app.audit_reviews(self.id, 'import', answer='{}', sample_id='old')['ok'])
        self.assertFalse(self.app._base.save_review_report(self.id, {**self.collection, 'id': 'old'}, {}))

    def test_json_with_unknown_review_numbers_discards_theme(self):
        sample = self.collection
        report = audit.parse_review_report(
            '{"summary":"","topics":[{"title":"Ложная тема","reviews":[7]}]}', sample
        )
        self.assertEqual(report['topics'], [])

    def test_telegram_prefills_supported_links_without_marking_sent(self):
        text = 'Здравствуйте! Ссылка? Да & нет.'
        for address in ('https://t.me/studio_name', 'https://t.me/+79995554433', 'tg://resolve?domain=studio_name'):
            self.assertEqual(parse_qs(urlsplit(draft_link(address, text)).query)['text'], [text])
        for address in ('https://t.me/+notAnActualInvite', 'https://t.me/studio_name/12', 'https://t.me/share'):
            self.assertEqual(draft_link(address, text), '')
        with patch.object(self.app, 'open_url') as opened:
            result = self.app.open_chat(self.lead, 'telegram', text_override=text)
        self.assertTrue(result['prefilled'])
        self.assertFalse(result['marked'])
        self.assertEqual(self.app._base.get(self.id)['status'], 'new')
        self.assertIn('?text=', opened.call_args.args[0])
