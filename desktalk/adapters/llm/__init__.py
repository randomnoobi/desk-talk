"""The AI behind the ChatModel interface: the OpenAI-compatible connection and the wrapper that keeps it safe.

    ai = OpenAICompatibleChatModel(config.llm, config.secrets, clock, replies=config.prompts.replies)
    model = ResilientChatModel(ai, clock, config.llm, time_limit_sec=ai.time_limit_sec, replies=config.prompts.replies)
    keepalive = ai.keepalive_worker()       # Ollama only; supervisor.add(keepalive, start_after=...)
"""

from desktalk.adapters.llm.errors import AIRefused, friendly_ai_error, is_rate_limit
from desktalk.adapters.llm.openai_compatible import OllamaKeepAlive, OpenAICompatibleChatModel
from desktalk.adapters.llm.resilient import ResilientChatModel

__all__ = ['AIRefused', 'OllamaKeepAlive', 'OpenAICompatibleChatModel', 'ResilientChatModel', 'friendly_ai_error',
           'is_rate_limit']
