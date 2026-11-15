import asyncio

import pytest

from app.utils.ethics import UnsafeTarget, validate_public_url


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://10.1.2.3/",
    "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
    "https://user:pass@example.com/",
    "http://localhost:5432/",
])
def test_manual_crawler_rejects_non_public_or_non_http_targets(url):
    with pytest.raises(UnsafeTarget):
        asyncio.run(validate_public_url(url))
