"""Сборка REST API роутеров."""

from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.config_api import router as config_router
from app.api.conversation_import import router as conversation_import_router
from app.api.conversations import router as conversations_router
from app.api.document_rag import router as document_rag_router
from app.api.health import router as health_router
from app.api.logs_api import router as logs_router
from app.api.messages import router as messages_router
from app.api.presets import router as presets_router
from app.api.prompt_macros import router as prompt_macros_router
from app.api.search import router as search_router
from app.api.sd_bridge import router as sd_bridge_router
from app.api.upload import router as upload_router
from app.api.users import router as users_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(health_router)
api_router.include_router(logs_router)
api_router.include_router(config_router)
api_router.include_router(conversations_router)
api_router.include_router(conversation_import_router)
api_router.include_router(document_rag_router)
api_router.include_router(search_router)
api_router.include_router(messages_router)
api_router.include_router(presets_router)
api_router.include_router(prompt_macros_router)
api_router.include_router(upload_router)
api_router.include_router(sd_bridge_router)
