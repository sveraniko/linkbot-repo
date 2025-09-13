from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from typing import Dict, Any, Optional
import asyncio
from datetime import datetime, timedelta

class MemoryStates(StatesGroup):
    """Состояния для подтверждения очистки памяти"""
    waiting_clear_confirmation = State()

class SimpleStateManager:
    """
    Простой менеджер состояний для подтверждений без использования FSM.
    Хранит временные состояния пользователей в памяти.
    """
    def __init__(self):
        self._states: Dict[int, Dict[str, Any]] = {}
        self._cleanup_interval = 300  # 5 минут
        self._cleanup_task = None
    
    def _ensure_cleanup_task(self):
        """Запускает задачу очистки если она еще не запущена"""
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                self._cleanup_task = asyncio.create_task(self._cleanup_expired_states())
            except RuntimeError:
                # Нет запущенного event loop, задача будет запущена позже
                pass
    
    async def _cleanup_expired_states(self):
        """Периодически очищает устаревшие состояния"""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                now = datetime.now()
                expired_users = []
                
                for user_id, state_data in self._states.items():
                    if 'expires_at' in state_data and now > state_data['expires_at']:
                        expired_users.append(user_id)
                
                for user_id in expired_users:
                    del self._states[user_id]
                    
            except Exception:
                pass  # Игнорируем ошибки в фоновой задаче
    
    def set_state(self, user_id: int, state: str, data: Optional[Dict[str, Any]] = None, timeout_seconds: int = 30):
        """Устанавливает состояние пользователя с таймаутом"""
        self._ensure_cleanup_task()  # Запускаем очистку если нужно
        expires_at = datetime.now() + timedelta(seconds=timeout_seconds)
        self._states[user_id] = {
            'state': state,
            'data': data or {},
            'expires_at': expires_at
        }
    
    def get_state(self, user_id: int) -> Dict[str, Any]:
        """Получает состояние пользователя"""
        return self._states.get(user_id, {})
    
    def clear_state(self, user_id: int):
        """Очищает состояние пользователя"""
        if user_id in self._states:
            del self._states[user_id]
    
    def has_state(self, user_id: int, state: str) -> bool:
        """Проверяет, находится ли пользователь в определенном состоянии"""
        user_state = self._states.get(user_id, {})
        return user_state.get('state') == state and datetime.now() < user_state.get('expires_at', datetime.min)

# Глобальный экземпляр менеджера состояний
state_manager = SimpleStateManager()