import unittest

import chat_server


class ChatModelRoutingTests(unittest.TestCase):
    def test_free_only_pick_rejects_paid_models(self):
        paid_model = {"id": "paid-model", "provider": "paid", "tier": "H"}

        self.assertIsNone(chat_server._pick([paid_model]))

    def test_free_vision_candidates_include_openrouter_image_models(self):
        original_models = list(chat_server.CHAT_MODELS)
        try:
            chat_server._update_global_models([
                {"id": "paid-vision", "label": "Paid Vision", "provider": "paid", "vision": True, "tier": "H"},
                {"id": "google/gemma-4-31b-it:free", "label": "Gemma 4", "provider": "openrouter", "free": True, "vision": True, "tier": "M"},
                {"id": "text-only:free", "label": "Text Only", "provider": "openrouter", "free": True, "tier": "M"},
            ])

            ids = [model["id"] for model in chat_server._free_vision_models()]

            self.assertIn("google/gemma-4-31b-it:free", ids)
            self.assertNotIn("paid-vision", ids)
            self.assertNotIn("text-only:free", ids)
        finally:
            chat_server._update_global_models(original_models)

    def test_free_vision_candidates_exclude_gemini_pro_by_default(self):
        ids = [model["id"] for model in chat_server._free_vision_models()]

        self.assertIn("gemini-2.5-flash", ids)
        self.assertIn("gemini-2.0-flash", ids)
        self.assertNotIn("gemini-2.5-pro", ids)
        self.assertNotIn("gemini-3-pro-preview", ids)


if __name__ == "__main__":
    unittest.main()
