# app/handlers/__init__.py
from aiogram import Router
from .base import router as base_router
from .menu import router as menu_router
from .status import router as status_router
from .ask import router as ask_router
from .import_file import router as import_router
from .keyboard import router as kb_router
from .chat import router as chat_router
from .answer_actions import router as ans_router
from .zip_handlers import router as zip_router
from .export import router as export_router
from .repo import router as repo_router
from .cleanup import router as cleanup_router

router = Router()
router.include_router(base_router)
router.include_router(menu_router)
router.include_router(status_router)
router.include_router(ask_router)
router.include_router(import_router)
router.include_router(kb_router)
router.include_router(chat_router)
router.include_router(ans_router)
router.include_router(zip_router)
router.include_router(export_router)
router.include_router(repo_router)
router.include_router(cleanup_router)