import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="alexpavsky-tests-"))
os.environ.setdefault("LANGFUSE_DISABLED", "1")

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("alexpavsky_chat_server", ROOT / "chat_server.py")
chat = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = chat
SPEC.loader.exec_module(chat)


class SupervisorTests(unittest.TestCase):
    def test_fast_paths_route_rag_news_and_smalltalk(self):
        self.assertEqual(chat._supervisor_route("hi", "chat")[0], ["smalltalk"])
        self.assertEqual(chat._supervisor_route("What projects has Alex built?", "chat")[0], ["rag"])
        self.assertEqual(chat._supervisor_route("What is new in AI today?", "chat")[0], ["news"])

    def test_short_factual_and_memory_questions_are_not_smalltalk(self):
        with patch.object(chat, "_supervisor_llm_enabled", return_value=False):
            self.assertNotEqual(chat._supervisor_route("What is my name?", "chat")[0], ["smalltalk"])
            self.assertNotEqual(chat._supervisor_route("Who is Alex?", "chat")[0], ["smalltalk"])

    def test_ambiguous_route_has_deterministic_fallback(self):
        with patch.object(chat, "_supervisor_llm_enabled", return_value=False):
            agents, reason, router = chat._supervisor_route(
                "Compare Alex's agent testing approach with this week's AI news", "chat"
            )
        self.assertEqual(agents, ["rag", "news"])
        self.assertEqual(reason, "keywords_rag_news")
        self.assertEqual(router, "heuristic_fallback")

    def test_supervisor_json_is_constrained_to_known_agents(self):
        agents, reason = chat._parse_supervisor_decision(
            '```json\n{"agents":["rag","shell","news","rag"],"reason":"mixed"}\n```'
        )
        self.assertEqual(agents, ["rag", "news"])
        self.assertEqual(reason, "mixed")

    def test_llm_cannot_misroute_quoted_greeting_as_smalltalk(self):
        with patch.object(chat, "_supervisor_llm_enabled", return_value=True), patch.object(
            chat, "_pick", return_value={"id": "router-test", "provider": "test"}
        ), patch.object(
            chat, "_call_model", return_value=('{"agents":["smalltalk"],"reason":"greeting"}', None)
        ):
            agents, _reason, router = chat._supervisor_route(
                "How do I say 'good morning' in Spanish?", "chat"
            )
        self.assertEqual(agents, ["general"])
        self.assertEqual(router, "heuristic_fallback")


class SpecialistTests(unittest.TestCase):
    def test_news_ranker_prefers_ai_category(self):
        articles = [
            {"title": "Unrelated web layout", "description": "", "source": "Dev", "category": "dev"},
            {"title": "New model evaluation", "description": "LLM release", "source": "AI", "category": "ai"},
        ]
        ranked = chat._news_rank_articles("What is new in AI today?", articles)
        self.assertEqual(ranked[0]["category"], "ai")

    def test_rag_extractive_fallback_only_uses_context(self):
        contexts = [
            "Promptfoo runs matrix evaluations across prompts and models. It supports assertions and CI quality gates.",
            "DeepEval supplies Python-native metrics for answer relevancy and faithfulness.",
        ]
        answer = chat._rag_extractive_fallback("What is promptfoo used for?", contexts, "chat")
        self.assertIn("Promptfoo", answer)
        self.assertIn("matrix evaluations", answer)
        self.assertNotIn("temporarily unavailable", answer)

    def test_rag_memory_fallback_quotes_previous_user_turn(self):
        history = [{"role": "user", "content": "My favourite testing tool is Playwright."}]
        answer = chat._rag_extractive_fallback(
            "Which testing tool did I say was my favourite?", ["Generic QA context."], "chat", history
        )
        self.assertIn("Playwright", answer)

    def test_generation_failure_degrades_to_grounded_extract(self):
        contexts = ["RAGAS evaluates faithfulness and answer relevancy for retrieval-augmented generation systems."]
        with patch.object(chat, "_agent_generate", return_value=("", None, {"code": "rate_limit"})):
            answer = chat._rag_generate_from_contexts("How is RAG evaluated?", contexts, "chat")
        self.assertIn("RAGAS", answer)
        self.assertNotIn("all providers failed", answer)

    def test_reasoning_and_moderation_artifacts_are_rejected(self):
        self.assertFalse(chat._reply_is_usable("Here's a thinking process:\n1. Analyze the user"))
        self.assertFalse(chat._reply_is_usable("User Safety: safe"))
        self.assertTrue(chat._reply_is_usable("Python decorators wrap functions to extend their behavior."))

    def test_rag_request_is_retrieval_only(self):
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "answer": "",
                    "contexts": ["Promptfoo runs repeatable prompt evaluations in CI."],
                    "sources": [{"filename": "eval-guide.md", "score": 0.91}],
                }).encode()

        def fake_urlopen(request, timeout):
            captured.update(json.loads(request.data.decode()))
            self.assertLessEqual(timeout, 25)
            return Response()

        with patch.object(chat, "urlopen", side_effect=fake_urlopen), patch.object(
            chat, "_agent_generate", return_value=("", None, {"code": "rate_limit"})
        ):
            answer, sources, raw = chat._call_agent_rag("What does Promptfoo do?", "chat")

        self.assertTrue(captured["retrieve_only"])
        self.assertIn("Promptfoo", answer)
        self.assertEqual(sources[0]["filename"], "eval-guide.md")
        self.assertTrue(raw["regenerated_locally"])


if __name__ == "__main__":
    unittest.main()


class GenerationPathTests(unittest.TestCase):
    """Run the real specialist generation code with only the network stubbed.

    The routing tests mock whole agents, so a NameError inside a generation
    path (the merge renamed SYSTEM_PROMPT) shipped green. These go through it.
    """

    def _fake_model(self, model, system, user_content, max_tok=1024, history=None):
        self.seen_system = system
        return "stub answer", None

    def test_general_agent_builds_prompt_and_answers(self):
        model = {"id": "stub:free", "label": "Stub", "provider": "openrouter", "free": True, "tier": "M"}
        with patch.object(chat, "_call_model", side_effect=self._fake_model), \
                patch.object(chat, "_route", return_value=(model, "M", "general")):
            for channel in ("chat", "voice"):
                answer = chat._call_agent_general("Explain Python decorators", channel, [], "")[0]
                self.assertEqual(answer, "stub answer")
                self.assertIn(chat.SYSTEM_PROMPT_BASE, self.seen_system)


class PrivacyTests(unittest.TestCase):
    def test_local_machine_paths_are_scrubbed(self):
        scrub = chat._scrub_local_paths
        self.assertEqual(scrub("see /Users/alexp/Projects/alexpavsky/chat_server.py"), "see alexpavsky/chat_server.py")
        self.assertEqual(scrub("`/Users/alexp/Projects/PW_alexpavsky` repo"), "`PW_alexpavsky` repo")
        self.assertEqual(scrub("/private/tmp/../Users/bob/notes.md"), "/private/tmp/..~/notes.md")
        self.assertEqual(scrub("/home/deploy/LocalApps/job-app/run.py"), "job-app/run.py")
        self.assertEqual(scrub("log at /Users/alexp/report.json"), "log at ~/report.json")
        for keep in ("run /usr/bin/python3", "GET /api/rag/query", "no paths here", ""):
            self.assertEqual(scrub(keep), keep)

    def test_rag_agent_output_is_scrubbed(self):
        leaky = "Alex built /Users/alexp/Projects/alexpavsky and /Users/alexp/Projects/PW_alexpavsky."
        payload = {"answer": "", "contexts": [leaky], "retrieve_only": True,
                   "sources": [{"content": leaky, "filename": "a.md", "score": 0.9}]}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(payload).encode()

        with patch.object(chat, "urlopen", return_value=FakeResponse()), \
                patch.object(chat, "_rag_generate_from_contexts", return_value=leaky):
            answer, sources, _ = chat._call_agent_rag("What projects has Alex built?", "chat")
        self.assertIn("alexpavsky", answer)
        self.assertNotIn("/Users/", answer)
        self.assertTrue(sources and all("/Users/" not in (src.get("content") or "") for src in sources))


class LanguageTests(unittest.TestCase):
    def test_reply_language_hint(self):
        self.assertEqual(chat._reply_language_hint("Чем занимается Алекс и какой у него опыт в QA?"), "\n\nAnswer in Russian.")
        self.assertEqual(chat._reply_language_hint("What projects has Alex built?"), "")
        self.assertEqual(chat._reply_language_hint(""), "")

    def test_rag_agent_prompt_pins_russian(self):
        captured = {}

        def fake_generate(system, user_content, **kwargs):
            captured["user"] = user_content
            return "ok", None, None

        with patch.object(chat, "_agent_generate", side_effect=fake_generate):
            chat._rag_generate_from_contexts("Чем занимается Алекс?", ["Alex builds QA tooling."], "chat")
        self.assertTrue(captured["user"].endswith("Answer in Russian."))

    def test_russian_questions_route_like_english(self):
        route = lambda q: chat._supervisor_route(q, "chat")[0]
        self.assertEqual(route("Какие проекты сделал Алекс?"), ["rag"])
        self.assertEqual(route("Расскажи про опыт Павловского в QA"), ["rag"])
        self.assertEqual(route("Что нового в мире AI?"), ["news"])

    def test_news_ranker_prefers_fresh_items_for_recency_questions(self):
        now = chat.datetime.now(chat.timezone.utc)
        iso = lambda days: (now - chat.timedelta(days=days)).isoformat()
        articles = [  # newest first, like the feed cache
            {"title": "EdgeMate: a local AI coding interviewer", "source": "Dev.to", "category": "dev", "date": iso(0.2)},
            {"title": "The latest AI news we announced", "source": "Google AI Blog", "category": "ai", "date": iso(3)},
            {"title": "New AI experts join the team", "source": "Google AI Blog", "category": "ai", "date": iso(17)},
        ]
        titles = [a["title"] for a in chat._news_rank_articles("What is new in AI today?", articles, limit=2)]
        self.assertIn("EdgeMate: a local AI coding interviewer", titles)
        self.assertNotIn("New AI experts join the team", titles)
        # Without recency intent the source-category preference is unchanged.
        top = chat._news_rank_articles("Google AI research", articles, limit=1)[0]
        self.assertEqual(top["source"], "Google AI Blog")
