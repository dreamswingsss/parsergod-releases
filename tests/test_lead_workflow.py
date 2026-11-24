import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app import audit, proposal, registry
from app.api import Api
from app.base import LeadBase, _SCHEMA
from app.enrich import _structured_review_texts
from app.tg import draft_link


class CompanyEvidenceTests(TestCase):
    def test_site_follows_details_and_records_source(self):
        home = 'https://studio.example/'
        details = home + 'about-us'
        pages = {
            home: '<a href="/about-us">Реквизиты</a><a href="https://other.example/legal">Оферта</a>',
            details: '<p>ООО «Блеск», ИНН 7707083893, ОГРН 1027700132195</p>',
        }

        def website(url, deadline):
            return url, pages[url], ''

        with patch('app.audit._website', side_effect=website):
            result = audit.check_site({'website': home})
        self.assertEqual(result['inns'], ['7707083893'])
        self.assertEqual(result['ogrns'], ['1027700132195'])
        self.assertEqual(result['legal_names'], ['ООО «Блеск»'])
        self.assertEqual(result['registry_sources']['7707083893'], details)

    def test_dadata_search_returns_candidates_with_city_priority(self):
        with patch('dadata.Dadata') as sdk:
            client = sdk.return_value.__enter__.return_value
            client.suggest.return_value = [{'value': 'ООО Блеск', 'data': {'inn': '7707083893'}}]
            result = registry.lookup('mock', 'Блеск', 'Москва')
        self.assertEqual(result[0]['inn'], '7707083893')
        client.suggest.assert_called_once_with(
            'party', 'Блеск', count=5, locations_boost=[{'city': 'Москва'}]
        )

    def test_exact_branch_uses_inn_and_kpp(self):
        with patch('dadata.Dadata') as sdk:
            client = sdk.return_value.__enter__.return_value
            client.find_by_id.return_value = []
            registry.lookup('mock', '7707083893', exact=True, kpp='540602001')
        client.find_by_id.assert_called_once_with('party', '7707083893/540602001', count=5)

    def test_manual_record_validates_both_numbers(self):
        values = {'number': '7707083893', 'other_number': '1027700132195', 'name': 'ООО Блеск'}
        record = registry.manual_record(values)
        self.assertEqual(record['inn'], '7707083893')
        self.assertEqual(record['ogrn'], '1027700132195')
        with self.assertRaises(registry.RegistryError):
            registry.manual_record({**values, 'other_number': '1027700132196'})

    def test_review_suggestions_ignore_unstructured_text(self):
        fixture = (
            '{"text":"Длинное описание организации для каталога"}'
            '{"id":1,"rating":3,"text":"Долго ждали расчёт стоимости."}'
            '{"@type":"Review","reviewBody":"Второй раз согласовывали время."}'
        )
        self.assertEqual(_structured_review_texts(fixture), [
            'Долго ждали расчёт стоимости.', 'Второй раз согласовывали время.'
        ])


class LeadWorkflowTests(TestCase):
    def test_old_database_and_keyless_import_feed_proposal(self):
        import sqlite3

        with TemporaryDirectory() as folder:
            path = Path(folder) / 'leads.db'
            with sqlite3.connect(path) as old:
                old.executescript(_SCHEMA)
            old.close()

            app = Api.__new__(Api)
            app._settings = {'output_dir': folder}
            app._base = LeadBase(path)
            self.addCleanup(app._base.close)
            lead = app._base.add({'name': 'Блеск <Студия>', 'city': 'Москва', 'url': 'https://2gis.ru/moscow/firm/42'})
            lid = lead['id']
            reviews = 'Долго ждали расчёт.\n\nДва дня ждали цену.\n\nУдобно приехать.'
            answer = json.dumps({
                'topics': [{'title': 'Долго отвечают на запрос цены', 'reviews': [1, 2]}],
                'offer_topic': 1, 'offer': 'Сделать форму расчёта', 'message': 'Здравствуйте! Могу показать форму.'
            })
            self.assertTrue(app.audit_reviews(lid, reviews, 'prompt')['ok'])
            self.assertTrue(app.audit_reviews(lid, reviews, 'import', answer, '2ГИС')['ok'])
            draft = app.audit_proposal(lid, {}, 'starter')['draft']
            self.assertEqual(draft['preset'], 'quote')
            self.assertEqual(draft['topic'], 'Долго отвечают на запрос цены')
            exported = app.audit_proposal(lid, {**draft, 'headline': '<Цена>'}, 'export')
            self.assertTrue(exported['ok'], exported)
            content = Path(exported['path']).read_text(encoding='utf-8')
            self.assertIn('Блеск &lt;Студия&gt;', content)
            self.assertIn('&lt;Цена&gt;', content)
            self.assertEqual(app.audit_open(lid)['proposal']['offer'], 'Сделать форму расчёта')
            self.assertFalse(app.audit_open(lid)['registry'])
            app._base.close()

    def test_unknown_review_theme_does_not_produce_offer(self):
        report = audit.parse_review_report(
            '{"topics":[{"title":"Ошибка","reviews":[2,2,18]}],"offer_topic":1,"offer":"Идея","message":"Текст"}',
            ['Первый', 'Второй', 'Третий'],
        )
        self.assertEqual(report['topics'], [])
        self.assertIsNone(report['offer_topic'])
        self.assertEqual(report['offer'], '')

    def test_telegram_prefills_supported_links_without_marking_sent(self):
        text = 'Здравствуйте! Ссылка? Да & нет.'
        for address in ('https://t.me/studio_name', 'https://t.me/+79995554433', 'tg://resolve?domain=studio_name'):
            self.assertEqual(parse_qs(urlsplit(draft_link(address, text)).query)['text'], [text])
        for address in ('https://t.me/+notAnActualInvite', 'https://t.me/studio_name/12', 'https://t.me/share'):
            self.assertEqual(draft_link(address, text), '')
        with TemporaryDirectory() as folder:
            app = Api.__new__(Api)
            app._base = LeadBase(Path(folder) / 'leads.db')
            try:
                row = app._base.add({'name': 'Блеск', 'telegram': 'https://t.me/studio_name'})
                with patch.object(app, 'open_url') as opened:
                    result = app.open_chat(row, 'telegram', text_override=text)
                self.assertTrue(result['prefilled'])
                self.assertFalse(result['marked'])
                self.assertEqual(app._base.get(row['id'])['status'], 'new')
                self.assertIn('?text=', opened.call_args.args[0])
            finally:
                app._base.close()

    def test_empty_proposal_uses_local_defaults(self):
        result = proposal.starter({'name': 'Блеск', 'category': 'Детейлинг'}, {})
        self.assertEqual(result['preset'], 'quote')
        self.assertIn('Блеск', result['message'])
