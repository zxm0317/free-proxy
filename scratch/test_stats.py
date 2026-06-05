import os
import sys

# Add root dir to path
sys.path.append(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from python_scripts.service import ProxyService

# Initialize Service
service = ProxyService(
    health_path="data/model-health.json",
    preferred_model_path="data/preferred-model.json",
    manual_order_path="data/manual-order.json",
    backups_dir="data/backups",
    health_ttl_seconds=30
)

# Call models_stats
res = service.models_stats()
print("Models stats result:")
import pprint
pprint.pprint(res)
