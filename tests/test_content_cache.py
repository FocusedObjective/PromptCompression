from app.content_cache import CachedTextCompression, ContentCompressionCache
from app.response_cache import LocalResponseCache
from app.tenant_profiles import TenantCompressionProfile


def test_content_cache_reuses_exact_text_without_storing_it_in_the_key():
    cache = ContentCompressionCache(
        LocalResponseCache(max_bytes=10_000, max_entry_bytes=2_000, ttl_seconds=60)
    )
    calls = 0
    secret_text = "private customer context that compresses"

    def compute() -> CachedTextCompression:
        nonlocal calls
        calls += 1
        return CachedTextCompression(
            text="private customer context compresses",
            original_tokens=6,
            compressed_tokens=5,
            changed=True,
            warnings=(),
        )

    arguments = dict(
        text=secret_text,
        role="user",
        model_name="test-model",
        aggressiveness=0.15,
        mode="deterministic",
        latency_budget_ms=None,
        tenant_profile=TenantCompressionProfile(tenant_id="tenant_1"),
        compute=compute,
    )
    first = cache.compress(**arguments)
    second = cache.compress(**arguments)

    assert calls == 1
    assert first.cache_status == "store"
    assert second.cache_status == "hit"
    assert second.text == "private customer context compresses"
    assert secret_text not in ContentCompressionCache._key(**{
        key: value for key, value in arguments.items() if key != "compute"
    })


def test_content_cache_separates_tenant_profiles():
    cache = ContentCompressionCache(
        LocalResponseCache(max_bytes=10_000, max_entry_bytes=2_000, ttl_seconds=60)
    )
    calls = 0

    def compute() -> CachedTextCompression:
        nonlocal calls
        calls += 1
        return CachedTextCompression(
            text="compressed",
            original_tokens=2,
            compressed_tokens=1,
            changed=True,
            warnings=(),
        )

    for tenant_id in ("tenant_1", "tenant_2"):
        cache.compress(
            text="same content",
            role="user",
            model_name="test-model",
            aggressiveness=0.15,
            mode="deterministic",
            latency_budget_ms=None,
            tenant_profile=TenantCompressionProfile(tenant_id=tenant_id),
            compute=compute,
        )

    assert calls == 2


def test_content_cache_does_not_store_integrity_rollbacks():
    cache = ContentCompressionCache(
        LocalResponseCache(max_bytes=10_000, max_entry_bytes=2_000, ttl_seconds=60)
    )
    calls = 0

    def compute() -> CachedTextCompression:
        nonlocal calls
        calls += 1
        return CachedTextCompression(
            text="deterministic fallback",
            original_tokens=4,
            compressed_tokens=2,
            changed=True,
            warnings=("output_rejected_integrity_identifier",),
        )

    arguments = dict(
        text="original text content here",
        role="user",
        model_name="test-model",
        aggressiveness=0.15,
        mode="model_auto",
        latency_budget_ms=None,
        tenant_profile=None,
        compute=compute,
    )
    assert cache.compress(**arguments).cache_status == "bypass"
    assert cache.compress(**arguments).cache_status == "bypass"
    assert calls == 2
