import unittest
from python_scripts.provider_catalog import PROVIDERS, get_provider, register_custom_provider

class TestCustomModels(unittest.TestCase):
    def test_register_custom_provider(self):
        # Register a custom model
        name = "custom-test-model"
        base_url = "http://localhost:11434/v1"
        api_key_env = "CUSTOM_KEY_custom-test-model"
        format = "openai"
        model_hints = ["test-model:latest"]

        # Initial length
        initial_len = len(PROVIDERS)

        register_custom_provider(name, base_url, api_key_env, format, model_hints)

        # Check that PROVIDERS list grew or updated
        self.assertIn(name, [p.name for p in PROVIDERS])
        provider = get_provider(name)
        self.assertEqual(provider.base_url, base_url)
        self.assertEqual(provider.api_key_env, api_key_env)
        self.assertEqual(provider.format, format)
        self.assertEqual(provider.model_hints, ("test-model:latest",))

    def test_proxy_service_custom_model(self):
        import os
        from pathlib import Path
        from python_scripts.service import ProxyService
        # Setup temporary database file
        db_file = Path("temp_test.db")
        if db_file.exists():
            db_file.unlink()
        db_url = f"sqlite:///{db_file}"
        os.environ['DATABASE_URL'] = db_url
        
        # Reset cache initialization in db_store
        import python_scripts.db_store as db_store
        db_store._cache_initialized = False
        
        svc = ProxyService(dotenv_path=".env")
        # Assert initial state has no custom models
        self.assertEqual(svc.get_custom_models(), [])
        
        # Add custom model
        res = svc.add_custom_model("http://custom-api/v1", "my-custom-model", "My Custom Model", "my-secret-key")
        self.assertTrue(res['ok'])
        cm_id = res['model']['id']
        
        # Assert registered
        customs = svc.get_custom_models()
        self.assertEqual(len(customs), 1)
        self.assertEqual(customs[0]['id'], cm_id)
        self.assertEqual(customs[0]['base_url'], "http://custom-api/v1")
        self.assertEqual(customs[0]['model'], "my-custom-model")
        
        # Assert adapter configuration works
        adapter = svc.provider_adapter(cm_id)
        self.assertEqual(adapter.provider.name, cm_id)
        self.assertEqual(adapter.provider.base_url, "http://custom-api/v1")
        self.assertEqual(adapter.api_key, "my-secret-key")
        
        # Update custom model key
        svc.update_custom_model_key(cm_id, "my-new-secret-key")
        customs2 = svc.get_custom_models()
        self.assertEqual(customs2[0]['api_key'], "my-new-secret-key")

        # Toggle active state
        self.assertIn(cm_id, svc.available_providers())
        svc.toggle_provider(cm_id, False)
        self.assertNotIn(cm_id, svc.available_providers())
        svc.toggle_provider(cm_id, True)
        self.assertIn(cm_id, svc.available_providers())

        # Clean up
        svc.delete_custom_model(cm_id)
        self.assertEqual(svc.get_custom_models(), [])
        
        if db_file.exists():
            db_file.unlink()

if __name__ == "__main__":
    unittest.main()
