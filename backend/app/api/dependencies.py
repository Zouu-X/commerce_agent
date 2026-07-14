from typing import Annotated
from uuid import UUID

from fastapi import Header

from app.agent.errors import ModelProviderError
from app.agent.provider import MockCommerceProvider, ModelProvider, OpenAICompatibleProvider
from app.commerce.context import CommerceContext
from app.core.config import get_settings


async def get_commerce_context(
    tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    store_id: Annotated[UUID, Header(alias="X-Store-Id")],
    customer_id: Annotated[UUID, Header(alias="X-Customer-Id")],
) -> CommerceContext:
    return CommerceContext(
        tenant_id=tenant_id,
        store_id=store_id,
        customer_id=customer_id,
    )


async def get_model_provider() -> ModelProvider:
    settings = get_settings()
    if settings.model_provider == "mock":
        return MockCommerceProvider()
    if settings.model_provider == "openai_compatible":
        if settings.model_api_key is None:
            raise ModelProviderError("model_api_key_missing")
        return OpenAICompatibleProvider(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key.get_secret_value(),
            model=settings.model_name,
        )
    raise ModelProviderError("unsupported_model_provider")
