from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from dialogue_session import DialogueSessionManager, infer_site_name, search_tavily


class DialogueSessionManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = DialogueSessionManager()

    def send(self, session_id: str, message: str, *, enable_external_search: bool = False):
        return self.manager.add_message(
            session_id,
            message,
            gemini_api_key="test-key",
            gemini_model="gemini-test",
            gemini_timeout=0.01,
            tavily_api_key="tavily-test-key",
            tavily_timeout=0.01,
            enable_external_search=enable_external_search,
        )

    def test_new_session_starts_with_site_question(self) -> None:
        session = self.manager.create(use_llm=False)

        self.assertEqual(session["phase"], "site")
        self.assertEqual(len(session["messages"]), 1)
        self.assertIn("対象", session["messages"][0]["content"])
        self.assertNotIn("トリップデータ", session["messages"][0]["content"])
        self.assertNotIn("名古屋市16区", session["messages"][0]["content"])
        self.assertFalse(session["simulation_draft"]["ready"])

    def test_rule_based_dialogue_extracts_nagoya_case(self) -> None:
        session = self.manager.create(use_llm=False)
        session = self.send(
            session["id"],
            "名鉄百貨店本店は閉業し、解体できないため空きフロアを暫定活用したい。",
        )

        self.assertEqual(session["plan"]["site"]["name"], "名鉄百貨店本店")
        self.assertIn("解体着工時期", session["plan"]["site"]["constraints"][0])
        self.assertEqual(session["phase"], "uses")
        self.assertIn("用途構成", session["messages"][-1]["content"])
        self.assertEqual(session["messages"][-1]["content"].count("？"), 1)

    def test_dialogue_advances_and_builds_simulation_draft(self) -> None:
        session = self.manager.create(use_llm=False)
        session_id = session["id"]
        self.send(session_id, "名鉄百貨店本店")
        self.send(session_id, "イベント・文化施設と飲食店を入れたい。")
        self.send(session_id, "駅利用者を対象にしたい。")
        session = self.send(
            session_id,
            "地上10階、延床面積15,000㎡、収容人数500人程度を利用する。",
        )

        self.assertEqual(session["plan"]["scale"]["floors"], 10)
        self.assertEqual(session["plan"]["scale"]["floor_area_sqm"], 15000.0)
        self.assertIn("200", session["simulation_draft"]["affected_purposes"])
        self.assertIn("400", session["simulation_draft"]["affected_purposes"])
        self.assertEqual(session["simulation_draft"]["target_label"], "名鉄百貨店本店")
        self.assertIn("接続動線", session["messages"][-1]["content"])
        self.assertEqual(session["messages"][-1]["content"].count("？"), 1)

    def test_named_proposal_expands_to_concrete_uses(self) -> None:
        session = self.manager.create(use_llm=False)
        session_id = session["id"]
        self.send(session_id, "名鉄百貨店本店")
        session = self.send(session_id, "Bの滞在交流型で、駅利用者と観光客を対象にしたい。")

        self.assertIn("イベント・文化", session["plan"]["intent"]["candidate_uses"])
        self.assertIn("飲食", session["plan"]["intent"]["candidate_uses"])
        self.assertNotIn("公共・地域利用", session["plan"]["intent"]["candidate_uses"])
        self.assertIn("200", session["simulation_draft"]["affected_purposes"])
        self.assertIn("400", session["simulation_draft"]["affected_purposes"])

    def test_sessions_are_independent(self) -> None:
        first = self.manager.create(use_llm=False)
        second = self.manager.create(use_llm=False)
        self.send(first["id"], "名古屋駅を対象にしたい。")

        self.assertEqual(self.manager.get(first["id"])["plan"]["site"]["name"], "名古屋駅")
        self.assertEqual(self.manager.get(second["id"])["plan"]["site"]["name"], "")

    def test_llm_response_rejects_ungrounded_numeric_assumptions(self) -> None:
        session = self.manager.create(use_llm=True)
        llm_response = {
            "assistant_message": "対象地を確認しました。導入したい用途を教えてください。",
            "updates": {
                "site": {"name": "名鉄百貨店本店", "existing_state": "閉業後"},
                "scale": {"floor_area_sqm": 10000},
            },
            "assumptions": [
                {
                    "field": "scale.floor_area_sqm",
                    "value": "10000",
                    "reason": "初期検討用",
                    "status": "proposed",
                }
            ],
            "confirmed_fields": [],
        }

        with patch("dialogue_session.call_gemini", return_value=llm_response):
            result = self.send(session["id"], "名鉄百貨店本店を対象にしたい。")

        self.assertEqual(result["llm_status"], "enabled")
        self.assertEqual(result["plan"]["site"]["name"], "名鉄百貨店本店")
        self.assertIsNone(result["plan"]["scale"]["floor_area_sqm"])
        self.assertEqual(result["assumptions"], [])
        self.assertIn("対象地を確認しました", result["messages"][-1]["content"])
        self.assertIn("用途構成", result["messages"][-1]["content"])
        self.assertNotIn("教えてください", result["messages"][-1]["content"])
        self.assertEqual(result["messages"][-1]["content"].count("？"), 1)

    def test_empty_llm_message_uses_rule_based_reply(self) -> None:
        session = self.manager.create(use_llm=True)
        llm_response = {
            "assistant_message": "  ",
            "updates": {"site": {"name": "名古屋駅"}},
            "assumptions": [],
            "confirmed_fields": [],
        }

        with patch("dialogue_session.call_gemini", return_value=llm_response):
            result = self.send(session["id"], "名古屋駅を対象にしたい。")

        self.assertEqual(result["llm_status"], "enabled")
        self.assertIn("用途構成", result["messages"][-1]["content"])

    def test_uses_answer_cannot_overwrite_site_or_skip_target_users(self) -> None:
        self.assertEqual(
            infer_site_name("買い物だけでなく食事や休憩のために駅利用者が立ち寄る施設にしたいです。"),
            "",
        )
        session = self.manager.create(use_llm=True)
        site_response = {
            "assistant_message": "対象地を名鉄百貨店本店として整理します。",
            "turn_type": "answer",
            "updates": {"site": {"name": "名鉄百貨店本店"}},
            "assumptions": [],
            "confirmed_fields": ["site.name"],
            "web_search_request": {"needed": False, "query": "", "reason": ""},
        }
        with patch("dialogue_session.call_gemini", return_value=site_response):
            session = self.send(session["id"], "名鉄百貨店本店")

        over_extracted_response = {
            "assistant_message": "駅利用者が立ち寄る施設という目的で整理します。",
            "turn_type": "answer",
            "updates": {
                "site": {"name": "買い物だけでなく食事や休憩のために駅"},
                "intent": {
                    "facility_type": "滞在型複合施設",
                    "candidate_uses": ["物販", "飲食", "休憩スペース"],
                    "target_users": ["駅利用者"],
                },
            },
            "assumptions": [],
            "confirmed_fields": [
                "site.name",
                "intent.facility_type",
                "intent.candidate_uses",
                "intent.target_users",
            ],
            "web_search_request": {"needed": False, "query": "", "reason": ""},
        }
        with patch("dialogue_session.call_gemini", return_value=over_extracted_response):
            result = self.send(
                session["id"],
                "買い物だけでなく食事や休憩のために駅利用者が立ち寄る施設にしたいです。",
            )

        self.assertEqual(result["plan"]["site"]["name"], "名鉄百貨店本店")
        self.assertIn("飲食", result["plan"]["intent"]["candidate_uses"])
        self.assertEqual(result["plan"]["intent"]["target_users"], [])
        self.assertEqual(result["phase"], "target_users")
        self.assertEqual(
            {
                item["field"]
                for item in result["messages"][-1]["applied_updates"]
            },
            {"intent.facility_type", "intent.candidate_uses"},
        )

    def test_dialogue_asks_one_field_at_a_time(self) -> None:
        session = self.manager.create(use_llm=False)
        session = self.send(session["id"], "名古屋駅")
        self.assertEqual(session["phase"], "uses")
        self.assertIn("用途構成", session["messages"][-1]["content"])
        self.assertEqual(session["messages"][-1]["content"].count("？"), 1)

        session = self.send(session["id"], "大型商業施設としてショッピングモールを導入する。")
        self.assertEqual(session["phase"], "target_users")
        self.assertEqual(session["plan"]["intent"]["facility_type"], "大型商業施設・ショッピングモール")
        self.assertIn("想定利用者", session["messages"][-1]["content"])
        self.assertEqual(session["messages"][-1]["content"].count("？"), 1)

    def test_clarification_does_not_advance_and_each_answer_reports_parameters(self) -> None:
        session = self.manager.create(use_llm=False)
        session_id = session["id"]
        session = self.send(session_id, "名古屋駅")
        self.assertEqual(
            session["messages"][-1]["applied_updates"][0]["field"],
            "site.name",
        )
        self.assertEqual(
            session["messages"][-1]["applied_updates"][0]["value"],
            "名古屋駅",
        )
        self.send(session_id, "飲食店とカフェを導入したい。")
        self.send(session_id, "駅利用者を対象にします。")
        self.send(session_id, "1-4階を利用します。")
        self.send(session_id, "延床面積は12,000㎡程度です。")
        self.send(session_id, "同時滞在800人程度を想定します。")
        self.send(session_id, "バスセンターと近鉄・JRの乗り換えを重視します。")
        self.send(session_id, "平日をより重視し、休日も対象にします。")
        session = self.send(session_id, "10-18時頃です。")

        self.assertEqual(session["phase"], "scenario")
        time_update = next(
            item
            for item in session["messages"][-1]["applied_updates"]
            if item["field"] == "operations.time_window"
        )
        self.assertEqual(time_update["value"], "10-18")

        session = self.send(session_id, "どういうことですか？")

        self.assertEqual(session["phase"], "scenario")
        self.assertEqual(session["plan"]["scenario"]["comparison_request"], "")
        self.assertEqual(session["messages"][-1]["turn_type"], "clarification")
        self.assertEqual(session["messages"][-1]["applied_updates"], [])
        self.assertIn("人流変化の想定強度", session["messages"][-1]["content"])
        self.assertIn("比較条件", session["messages"][-1]["content"])

    def test_final_confirmation_does_not_repeat_confirmation_question(self) -> None:
        session = self.manager.create(use_llm=False)
        session_id = session["id"]
        for answer in [
            "名古屋駅",
            "飲食店を導入したい。",
            "駅利用者を対象にします。",
            "4階を利用します。",
            "延床面積は12,000㎡です。",
            "収容人数は800人です。",
            "バスセンターとの接続を重視します。",
            "平日を重視します。",
            "10-18時です。",
            "控えめなケースにします。",
        ]:
            session = self.send(session_id, answer)

        self.assertEqual(session["phase"], "ready")
        self.assertFalse(session["plan_confirmed"])
        self.assertFalse(session["simulation_draft"]["ready"])
        self.assertIn("確認", session["messages"][-1]["content"])

        session = self.send(session_id, "はい、この内容で確定します。")

        self.assertTrue(session["plan_confirmed"])
        self.assertEqual(session["phase_label"], "計画条件確定")
        self.assertEqual(session["messages"][-1]["turn_type"], "confirmation")
        self.assertNotIn("確認：", session["messages"][-1]["content"])
        self.assertNotIn("？", session["messages"][-1]["content"])
        draft = session["simulation_draft"]
        self.assertTrue(draft["ready"])
        self.assertTrue(draft["scenario_text"].startswith("構造化された計画条件"))
        self.assertIn("対象地: 名古屋駅", draft["scenario_text"])
        self.assertNotIn("この内容で確定します", draft["scenario_text"])
        self.assertEqual(draft["affected_purposes"], ["200"])
        self.assertEqual(draft["affected_ratio"], 0.05)
        self.assertEqual(draft["strength"], 0.20)
        self.assertEqual(draft["influence_radius_km"], 2.0)
        self.assertEqual(
            draft["parameter_sources"]["affected_purposes"],
            "intent.candidate_uses",
        )

    def test_gemini_failure_falls_back_without_losing_message(self) -> None:
        session = self.manager.create(use_llm=True)
        session = self.send(session["id"], "名古屋駅を対象にしたい。")

        self.assertEqual(session["llm_status"], "fallback")
        self.assertEqual(session["plan"]["site"]["name"], "名古屋駅")
        self.assertTrue(session["notes"])

    def test_gemini_grounding_is_attached_to_assistant_message(self) -> None:
        session = self.manager.create(use_llm=True, llm_model="gemini-3.1-flash-lite")
        llm_response = {
            "assistant_message": "営業状況をWebで確認しました。",
            "updates": {"site": {"name": "名鉄百貨店本店"}},
            "assumptions": [],
            "confirmed_fields": [],
            "_grounding": {
                "used": True,
                "queries": ["名鉄百貨店本店 営業状況"],
                "sources": [{"title": "公式サイト", "url": "https://example.com/"}],
                "search_entry_point": "",
            },
        }

        with patch("dialogue_session.call_gemini", return_value=llm_response):
            result = self.send(session["id"], "名鉄百貨店本店の現況も確認して。")

        self.assertEqual(result["web_search_count"], 1)
        self.assertTrue(result["messages"][-1]["web_search_used"])
        self.assertEqual(result["messages"][-1]["web_sources"][0]["title"], "公式サイト")

    def test_tavily_search_is_used_only_when_gemini_requests_it(self) -> None:
        session = self.manager.create(
            use_llm=True,
            llm_model="gemini-3.1-flash-lite",
            external_search_enabled=True,
        )
        first_response = {
            "assistant_message": "現況はWeb確認が必要です。",
            "updates": {"site": {"name": "名鉄百貨店本店"}},
            "assumptions": [],
            "confirmed_fields": [],
            "web_search_request": {
                "needed": True,
                "query": "名鉄百貨店本店 現況 再開発",
                "reason": "現在情報の確認",
            },
        }
        searched_response = {
            "assistant_message": "検索結果では再開発計画の見直しが公表されています。",
            "updates": {"site": {"name": "名鉄百貨店本店"}},
            "assumptions": [],
            "confirmed_fields": [],
            "web_search_request": {"needed": False, "query": "", "reason": ""},
        }
        tavily_result = {
            "used": True,
            "provider": "Tavily",
            "query": "名鉄百貨店本店 現況 再開発",
            "queries": ["名鉄百貨店本店 現況 再開発"],
            "results": [{"title": "公式発表", "url": "https://example.com/", "content": "見直し"}],
            "sources": [{"title": "公式発表", "url": "https://example.com/"}],
            "credits_used": 1,
        }

        with (
            patch("dialogue_session.call_gemini", side_effect=[first_response, searched_response]) as gemini,
            patch("dialogue_session.search_tavily", return_value=tavily_result) as tavily,
        ):
            result = self.send(
                session["id"],
                "名鉄百貨店本店の現在の状況も確認して。",
                enable_external_search=True,
            )

        self.assertEqual(gemini.call_count, 2)
        tavily.assert_called_once()
        self.assertEqual(result["web_search_count"], 1)
        self.assertEqual(result["web_search_credits_used"], 1)
        self.assertEqual(result["messages"][-1]["web_search_provider"], "Tavily")
        self.assertEqual(result["messages"][-1]["web_sources"][0]["title"], "公式発表")
        self.assertIn("検索結果では", result["messages"][-1]["content"])

    def test_real_building_floor_count_accepts_only_web_grounded_area_estimate(self) -> None:
        session = self.manager.create(
            use_llm=True,
            llm_model="gemini-3.1-flash-lite",
            external_search_enabled=True,
        )
        setup_response = {
            "assistant_message": "名鉄百貨店本店の暫定利用として整理します。",
            "updates": {
                "site": {"name": "名鉄百貨店本店"},
                "intent": {
                    "candidate_uses": ["飲食"],
                    "target_users": ["駅利用者"],
                },
            },
            "assumptions": [],
            "confirmed_fields": [],
            "web_search_request": {"needed": False, "query": "", "reason": ""},
        }
        with patch("dialogue_session.call_gemini", return_value=setup_response):
            self.send(
                session["id"],
                "名鉄百貨店本店",
                enable_external_search=True,
            )
            self.send(
                session["id"],
                "飲食施設と休憩スペースを導入したい。",
                enable_external_search=True,
            )
            self.send(
                session["id"],
                "駅利用者を対象にしたい。",
                enable_external_search=True,
            )

        first_response = {
            "assistant_message": "4階分なら延床面積は1万㎡程度が考えられます。次は動線を決めます。",
            "updates": {"scale": {"floors": 4, "floor_area_sqm": 10000}},
            "assumptions": [
                {
                    "field": "scale.floor_area_sqm",
                    "value": "10000",
                    "reason": "一般的な商業施設として仮定",
                    "status": "proposed",
                }
            ],
            "confirmed_fields": ["scale.floor_area_sqm"],
            "web_search_request": {"needed": False, "query": "", "reason": ""},
        }
        searched_response = {
            "assistant_message": "4階分を使う案として整理します。公開資料の売場面積を単純換算すると約12,000㎡ですが、階別面積は確認できないため参考値です。次は動線を考えます。",
            "updates": {"scale": {"floors": 4, "floor_area_sqm": 12000}},
            "assumptions": [
                {
                    "field": "scale.floor_area_sqm",
                    "value": "約12,000㎡",
                    "reason": "公開されている売場面積を階数で単純換算",
                    "status": "proposed",
                }
            ],
            "confirmed_fields": ["scale.floor_area_sqm"],
            "web_search_request": {"needed": False, "query": "", "reason": ""},
        }
        tavily_result = {
            "used": True,
            "provider": "Tavily",
            "query": "名鉄百貨店本店 建物概要 延床面積 売場面積 フロア面積 階数",
            "queries": ["名鉄百貨店本店 建物概要 延床面積 売場面積 フロア面積 階数"],
            "results": [
                {
                    "title": "名鉄百貨店 会社概要",
                    "url": "https://example.com/meitetsu",
                    "content": "売場面積の公開情報",
                }
            ],
            "sources": [
                {"title": "名鉄百貨店 会社概要", "url": "https://example.com/meitetsu"}
            ],
            "credits_used": 1,
        }

        with (
            patch(
                "dialogue_session.call_gemini",
                side_effect=[first_response, searched_response],
            ) as gemini,
            patch("dialogue_session.search_tavily", return_value=tavily_result) as tavily,
        ):
            result = self.send(
                session["id"],
                "名鉄百貨店の低層部分、4階建てくらいかな",
                enable_external_search=True,
            )

        self.assertEqual(gemini.call_count, 2)
        self.assertIn("名鉄百貨店本店", tavily.call_args.args[0])
        self.assertEqual(result["plan"]["scale"]["floors"], 4)
        self.assertEqual(result["plan"]["scale"]["floor_area_sqm"], 12000.0)
        self.assertEqual(result["assumptions"][0]["status"], "proposed")
        self.assertIn("Web根拠", result["assumptions"][0]["reason"])
        self.assertEqual(result["phase"], "capacity")
        self.assertIn("収容人数", result["messages"][-1]["content"])
        self.assertNotIn("次は", result["messages"][-1]["content"])
        self.assertEqual(result["web_search_count"], 1)

    def test_missing_scale_values_are_asked_before_connections(self) -> None:
        session = self.manager.create(use_llm=False)
        session_id = session["id"]
        self.send(session_id, "名鉄百貨店本店")
        self.send(session_id, "飲食施設と休憩スペース")
        self.send(session_id, "駅利用者")

        session = self.send(session_id, "1-4階を利用します。")
        self.assertEqual(session["phase"], "floor_area")
        self.assertIn("床面積", session["messages"][-1]["content"])

        session = self.send(session_id, "延床面積は12,000㎡程度です。")
        self.assertEqual(session["phase"], "capacity")
        self.assertIn("収容人数", session["messages"][-1]["content"])

        session = self.send(session_id, "同時滞在800人程度です。")
        self.assertEqual(session["phase"], "connections")
        self.assertIn("接続動線", session["messages"][-1]["content"])

    def test_tavily_client_is_fixed_to_one_credit_basic_search(self) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "results": [
                    {
                        "title": "名古屋市公式",
                        "url": "https://example.com/nagoya",
                        "content": "計画の概要",
                        "score": 0.9,
                    }
                ],
                "usage": {"credits": 1},
            }
        ).encode("utf-8")
        response.__enter__.return_value = response

        with patch("dialogue_session.urllib.request.urlopen", return_value=response) as urlopen:
            result = search_tavily("名古屋駅 再開発", "test-key", 1.0)

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["search_depth"], "basic")
        self.assertFalse(payload["auto_parameters"])
        self.assertTrue(payload["include_usage"])
        self.assertEqual(result["credits_used"], 1)
        self.assertEqual(result["sources"][0]["title"], "名古屋市公式")

    def test_unknown_session_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.get("missing")


if __name__ == "__main__":
    unittest.main()
